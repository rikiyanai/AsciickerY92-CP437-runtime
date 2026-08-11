#pragma once

// =============================================================================
// Game Architecture - Main Header
// =============================================================================
// This file defines the core data structures for the game engine.
// The engine uses a C-style struct-based architecture (Data-Oriented Design)
// rather than heavy C++ OOP.
//
// Key Components:
// - Game: The God Object holding global state, input, and subsystems.
// - Human/Character: Entity state for players and NPCs.
// - Input: Raw and high-level input accumulation (Mouse/Keyb/Touch/Gamepad).
// - Server: Multiplayer sync logic (lag compensation, command processing).
// =============================================================================

#include "physics.h"
#include "interaction_query.h"
#include "render.h"
#include "sprite.h"
#include "world.h"
#include "terrain.h"
#include "actor_visual_profile.h"
#include "authoritative_item_state.h"
#include "mount_state_types.h"

struct InputState;

#include "inventory.h"
#include "protocol/protocol_common.h"
#include "multiplayer_protocol.h"
#include "mp_move.h"
#include "server_connection.h"
#include "server_authority.h"

enum GAME_KEYB
{
	KEYB_DOWN,
	KEYB_UP,
	KEYB_CHAR,
	KEYB_PRESS, // non-char terminal input with modifiers
};

enum GAME_MOUSE
{
	MOUSE_MOVE,
	MOUSE_LEFT_BUT_DOWN,
	MOUSE_LEFT_BUT_UP,
	MOUSE_RIGHT_BUT_DOWN,
	MOUSE_RIGHT_BUT_UP,
	MOUSE_MIDDLE_BUT_DOWN,
	MOUSE_MIDDLE_BUT_UP,
	MOUSE_WHEEL_DOWN,
	MOUSE_WHEEL_UP
};

enum GAME_TOUCH
{
	TOUCH_MOVE,
	TOUCH_BEGIN,
	TOUCH_END,
	TOUCH_CANCEL
};

void Buzz();

struct Game;

#include "item_owner.h"
#include "character.h"

#include "talkbox.h"
#include "../server/combat_event_server_state.h"
#include "../server/snapshot_client_state.h"
#include "../server/snapshot_npc_repository.h"
#include "../server/authoritative_item_server_state.h"

#include "menu.h"
#include "snapshot_client/remote_snapshot_presentation_track.h"
#include "snapshot_client/local_snapshot_presentation_track.h"
#include "human.h"

// -----------------------------------------------------------------------------
// Global Server State
// -----------------------------------------------------------------------------
// Server composes two ownership sub-structs:
// - ServerConnection (transport, session identity, lag telemetry)
// - ServerAuthority  (player roster, snapshots, items, combat)
// Access fields via server->connection.* or server->authority.*.
struct Server
{
	// ── Transport/session methods ──
	bool Proc(const uint8_t* ptr, int size); // called directly by JS (implemented in game.cpp)
	void Proc(); // does nothing on JS, native apps calls above func for all queued commands
	bool Send(const uint8_t* data, int size); // implemented by game_app/game_web
	void Log(const char* str);

	// ── Composed ownership sub-structs ──
	ServerConnection connection;
	ServerAuthority authority;
};

extern Server* volatile server; // global!
extern Game* prime_game; // current live game instance used by renderer diagnostics
bool HasAuthoritativeServerSession(const Game* g);
void DestroySnapshotNpcVisuals(ServerSnapshotNpcRepository* repo);
void ServerDestroyAuthoritativeItemVisuals(Server* s);

#include "input_state.h"
#include "camera_state.h"
#include "ui_state.h"

#include "inventory_view_state.h"

#include "authoritative_client_state.h"

#include "game_session.h"

#include "local_player_state.h"

#include "debug_telemetry_state.h"
// -----------------------------------------------------------------------------
// The Game God Object
// -----------------------------------------------------------------------------
struct Game
{
	// terrain & world are global
	// World* world;
	// Terrain* terrain;

	using Input = InputState;
	using TalkMem = UiState::TalkMem;
	using ConsumeAnim = InventoryViewState::ConsumeAnim;
	using DebugInputEvent = DebugTelemetryState::DebugInputEvent;

	DebugTelemetryState debug;

	uint64_t stamp;

	GameSession session;
	AuthoritativeClientState authoritative;
	UiState ui;
	CameraState camera;

	Renderer* renderer;
	Physics* physics;

	LocalPlayerState player;
	InventoryViewState inventory_view;
	bool DropItem(int index);
	bool PickItem(Item* item);
	bool CheckDrop(int c/*contact index*/, int xy[2]=0, AnsiCell* ptr=0, int w=0, int h=0);
	int CheckPick(const int pos[2]);

	void CancelItemContacts();
	void ExecuteItem(int my_item);

	void StartContact(int id, int x, int y, int b);
	void MoveContact(int id, int x, int y);
	void EndContact(int id, int x, int y);

	int GetContact(int id);

	Input input;

	// just accumulates input
	void OnKeyb(GAME_KEYB keyb, int key);
	void OnMouse(GAME_MOUSE mouse, int x, int y);
	void OnTouch(GAME_TOUCH touch, int id, int x, int y);
	void OnFocus(bool set);
	void OnSize(int w, int h, int fw, int fh);
	void OnMessage(const uint8_t* msg, int len);

	void OnPadMount(bool connect);
	void OnPadButton(int b, bool down);
	void OnPadAxis(int a, int16_t pos);

	// update physics with accumulated input then render state to output
	void Render(uint64_t _stamp, AnsiCell* ptr, int width, int height);
	void ResetRenderDebugTelemetry(uint64_t _stamp);
	void LatchRemoteVisibilityIssue(uint64_t _stamp, bool* issue_seen_this_frame);
	void PublishCompletedFrameDebugTelemetry();
	void ScreenToCell(int p[2]) const;

	// Local-player authority seam — declared in local_player_authority.h
	// (moved out of Game to make coupling visible, FL-2731)


	void MenuKeyb(GAME_KEYB keyb, int key);
	void MenuMouse(GAME_MOUSE mouse, int x, int y);
	void MenuTouch(GAME_TOUCH touch, int id, int x, int y);
	void MenuPadMount(bool connected);
	void MenuPadButton(int b, bool down);
	void MenuPadAxis(int a, int16_t pos);

	void OpenMenu(int method);
	void CloseMenu();
	void ToggleMenu(int method);
	void PaintMenu(AnsiCell* ptr, int width, int height);
	int  HitMenu(int hx, int hy);
};

Game* CreateGame();
void DeleteGame(Game* g);
inline bool LocalPlayerAuthoritativePoseReady(const LocalPlayerState& player, bool is_server_session)
{
	// FL-933 / FL-394: !server means single-player / no authoritative session, not
	// "multiplayer bootstrap complete". Multiplayer callers that care about gameplay
	// readiness still need accepted authoritative snapshot state, not just this helper.
	return !is_server_session ||
		(player.authoritative_snapshot_valid &&
		 MpMoveHasAuthoritativeSnapshot(&player.mp_move));
}

void InitGame(Game* g, int water, float pos[3], float yaw, float dir, float lt[4], uint64_t stamp);
void FreeGame(Game* g);

// Return the count of available skin_definition_ids in the compiled bundle and
// fill |out_ids| (up to |max_ids|) with their numeric ids.
int GameGetBundleSkinIds(uint16_t* out_ids, int max_ids);
void PaintTerrain(float* xy, float r, int matid);
void GamePadMount(const char* name, int axes, int buttons, const uint8_t map[]);
void GamePadUnmount();
void GamePadButton(int b, int16_t pos);
void GamePadAxis(int a, int16_t pos);

extern uint64_t (*MakeStamp)();

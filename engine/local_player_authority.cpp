// local player movement IO extracted from game.cpp
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
#include "local_player_authority.h"
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
// FL-4137 #21: #include "mp_diag_shadow_colliders.h" DELETED. TU removed.
#include "snapshot_client/local_snapshot_presentation_track.h"
#include "physics_commands.h"
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
#include "snapshot_client/snapshot_npc_visual_lifecycle.h"
#include "matrix.h"
#include "fast_rand.h"
#include "font1.h"
#include "gamepad.h"
#include "audio.h"
#include "game_api.h"
#ifdef __EMSCRIPTEN__
extern uint64_t GetTime();
#endif
#include "lexer.h"
#include "mainmenu.h"
#include "weather.h"

extern Terrain* terrain;
extern World* world;
extern Material mat[256];
extern char base_path[];

// ── Phase 1: Read pose from physics or fall back to player state ──────────
static void ReadPoseOrFallback(Physics* physics, LocalPlayerState& player,
	bool authoritative_session, float session_water, PhysicsIO* io)
{
	io->yaw = player.prev_yaw;
	if (physics)
	{
		PhysicsPose pose = PhysicsReadPose(physics);
		if (pose.valid)
		{
			io->pos[0] = pose.pos[0];
			io->pos[1] = pose.pos[1];
			io->pos[2] = pose.pos[2];
			io->player_dir = pose.dir;
			io->grounded = pose.grounded;
			if (!authoritative_session && isfinite(pose.yaw))
				io->yaw = pose.yaw;
		}
	}
	else
	{
		io->pos[0] = player.pos[0]; io->pos[1] = player.pos[1]; io->pos[2] = player.pos[2];
		io->player_dir = player.dir;
		io->grounded = player.prev_grounded;
	}
	io->water = session_water;
	io->jump = false;
}

// ── Phase 2: Apply touch-contact forces (FORCE / TORQUE) ──────────────────
// Returns {force_handled, torque_handled, torque_sign} consumed by later phases.
struct ContactForceResult { bool force_handled; int torque_handled; int torque_sign; };

static ContactForceResult ApplyContactForces(InputState& input, CameraState& camera,
	const GameSession& session, LocalPlayerState& player, Physics* physics,
	bool is_server_session, uint64_t _stamp, uint64_t stamp, PhysicsIO* io)
{
	ContactForceResult r = {false, 0, 0};

	for (int i = 0; i < 4; i++)
	{
		switch (input.contact[i].action)
		{
			case InputState::Contact::FORCE:
			{
				assert(!r.force_handled);
				r.force_handled = true;
				if (i == 0)
				{
					io->x_force = 2 * ((input.contact[i].pos[0] * 2 - input.size[0]) / (float)input.size[0] - 2 * camera.scene_shift / 2 / (float)session.render_size[0]);
					io->y_force = 2 * ((input.size[1] - input.contact[i].pos[1] * 2) / (float)input.size[1]);
				}
				else
				{
					io->x_force = 4 * (input.contact[i].pos[0] - input.contact[i].drag_from[0]) / (float)input.size[0];
					io->y_force = 4 * (input.contact[i].drag_from[1] - input.contact[i].pos[1]) / (float)input.size[0];
				}

				float len = sqrtf(io->x_force * io->x_force + io->y_force * io->y_force);
				if (len > 1)
				{
					io->x_force /= len;
					io->y_force /= len;
				}
				break;
			}

			case InputState::Contact::TORQUE:
				if (i == 0)
				{
					assert(r.torque_handled == 0);
					r.torque_handled = 2;
					float sensitivity = 200.0f / input.size[0];
					float yaw = input.contact[i].start_yaw - sensitivity * (input.contact[i].pos[0] - input.contact[i].drag_from[0]);
					io->torque = 0;

					double dt = (_stamp - stamp) * 0.000001 * 20;
					player.yaw_vel = (yaw - player.prev_yaw);
					if (dt < 0)
						dt = 0;
					else if (dt > 1)
						dt = 1;
					yaw = (float)(player.prev_yaw + player.yaw_vel * dt);
					while (yaw > 180.0f) yaw -= 360.0f;
					while (yaw < -180.0f) yaw += 360.0f;
					// FL-1733: touch drag rotation also suppresses snapshot yaw resync
					player.last_torque_active_stamp = _stamp;
					if (is_server_session)
					{
						io->yaw = yaw;
						io->torque = 1000000.0f;
						player.yaw_vel = 0.0f;
					}
					else
					{
						PhysicsTeleportCommand command = {};
						command.set_yaw = true;
						command.yaw = yaw;
						command.yaw_vel = 0.0f;
						PhysicsTeleport(physics, command);
					}
				}
				else
				{
					assert(r.torque_handled != 2);
					r.torque_handled = 1;
					r.torque_sign += input.contact[i].margin;
				}
				break;
		}
	}

	io->torque = (float)(r.torque_sign < 0 ? -1 : r.torque_sign > 0 ? +1 : 0);
	return r;
}

// ── Phase 3: Apply keyboard forces (WASD/arrows, torque keys, fly) ────────
static void ApplyKeyboardForces(InputState& input, LocalPlayerState& player,
	const GameSession& session, const UiState& ui, DebugTelemetryState& debug,
	CameraState& camera, bool force_handled, int torque_handled,
	int server_local_id, bool is_server_session,
	uint64_t _stamp, uint64_t stamp, PhysicsIO* io)
{
	if (player.talk_box)
		return;

	if (!force_handled)
	{
		float speed = 1;
		if (input.IsKeyDown(A3D_LSHIFT) || input.IsKeyDown(A3D_RSHIFT))
			speed *= 0.5;
		static int fljit_keystate_logs = 0;
		int w_down = input.IsKeyDown(A3D_W) ? 1 : 0;
		int a_down = input.IsKeyDown(A3D_A) ? 1 : 0;
		int s_down = input.IsKeyDown(A3D_S) ? 1 : 0;
		int d_down = input.IsKeyDown(A3D_D) ? 1 : 0;
		debug.dbg_input_w_down = w_down;
		debug.dbg_input_a_down = a_down;
		debug.dbg_input_s_down = s_down;
		debug.dbg_input_d_down = d_down;
		debug.dbg_main_menu_active = ui.main_menu ? 1 : 0;
		debug.dbg_show_inventory_active = ui.show_inventory ? 1 : 0;
		debug.dbg_talk_box_active = player.talk_box ? 1 : 0;
		debug.dbg_menu_depth_value = ui.menu_depth;
		debug.dbg_server_local_id = server_local_id;
		if (is_server_session && fljit_keystate_logs < 80 && (w_down || a_down || s_down || d_down || input.PressKey))
		{
			printf("[FLJIT-KEYSTATE] stamp=%llu main_menu=%d ui.menu_depth=%d talk=%d inv=%d press=%d w=%d a=%d s=%d d=%d\n",
				(unsigned long long)_stamp,
				ui.main_menu ? 1 : 0, ui.menu_depth, player.talk_box ? 1 : 0, ui.show_inventory ? 1 : 0,
				input.PressKey, w_down, a_down, s_down, d_down);
			fflush(stdout);
			fljit_keystate_logs++;
		}

		if (ui.show_inventory)
		{
			io->x_force = (float)((int)input.IsKeyDown(A3D_D) - (int)input.IsKeyDown(A3D_A));
			io->y_force = (float)((int)input.IsKeyDown(A3D_W) - (int)input.IsKeyDown(A3D_S));
		}
		else
		{
			io->x_force = (float)((int)(input.IsKeyDown(A3D_RIGHT) || input.IsKeyDown(A3D_D)) - (int)(input.IsKeyDown(A3D_LEFT) || input.IsKeyDown(A3D_A)));
			io->y_force = (float)((int)(input.IsKeyDown(A3D_UP) || input.IsKeyDown(A3D_W)) - (int)(input.IsKeyDown(A3D_DOWN) || input.IsKeyDown(A3D_S)));
		}

		float len = sqrtf(io->x_force * io->x_force + io->y_force * io->y_force);
		if (len > 0)
			speed /= len;
		io->x_force *= speed;
		io->y_force *= speed;
	}

	if (ui.menu_depth < 0)
	{
		if (input.contact[3].action == InputState::Contact::NONE)
		{
			io->x_force += input.pad_axis[0] / 1024 / 32.0f;
			io->y_force -= input.pad_axis[1] / 1024 / 32.0f;
		}
	}

	if (!torque_handled)
	{
		io->torque = (float)((int)(input.IsKeyDown(A3D_INSERT) || input.IsKeyDown(A3D_PAGEDOWN) || input.IsKeyDown(A3D_F2) || input.IsKeyDown(A3D_E)) -
			(int)(input.IsKeyDown(A3D_DELETE) || input.IsKeyDown(A3D_PAGEUP) || input.IsKeyDown(A3D_F1) || input.IsKeyDown(A3D_Q)));

		if (session.fly_mode)
		{
			io->z_force = (float)((int)(input.IsKeyDown(A3D_2)) - (int)(input.IsKeyDown(A3D_X)));
			io->z_force *= 20.0f;
		}
		else
		{
			io->z_force = 0;
			if (input.IsKeyDown(A3D_I) || input.IsKeyDown(A3D_2))
				camera.cam_shift -= (int)(1 + (_stamp - stamp) / 8264);
			if (input.IsKeyDown(A3D_X))
				camera.cam_shift += (int)(1 + (_stamp - stamp) / 8264);
		}
		io->fly = session.fly_mode;
	}
}

// ── Phase 4: Merge gamepad axis into forces / torque ─────────────────────
static void MergeGamepadInput(const int16_t* pad_axis, int menu_depth, PhysicsIO* io)
{
	if (menu_depth < 0)
		io->torque += (pad_axis[4] - pad_axis[5]) / 1024 / 32.0f;
}

// ── Phase 5: Finalize IO — API merge, mount clamp, yaw integration, snap ──
static void FinalizeIO(const InputState& input, LocalPlayerState& player,
	DebugTelemetryState& debug, uint64_t _stamp, uint64_t stamp,
	bool is_server_session, int torque_handled, PhysicsIO* io)
{
	io->jump = input.jump;
	debug.dbg_io_jump_requested = io->jump ? 1 : 0;

	io->x_force = io->x_force * (1 - input.api_move[2]) + input.api_move[0];
	io->y_force = io->y_force * (1 - input.api_move[2]) + input.api_move[1];
	debug.dbg_io_x_force = io->x_force;
	debug.dbg_io_y_force = io->y_force;
	debug.dbg_io_z_force = io->z_force;
	debug.dbg_io_torque = io->torque;
	debug.dbg_player_action_value = (int)player.combat_state;

	uint8_t local_mount_state = (uint8_t)player.mount_state;
	if (player.prev_grounded && local_mount_state == MOUNT::BEE)
	{
		float len = sqrtf(io->x_force * io->x_force + io->y_force * io->y_force);
		if (len > 0.5f)
		{
			float mul = 0.5f / len;
			io->x_force *= mul;
			io->y_force *= mul;
		}
	}

	float dt_sec = 0.0f;
	if (_stamp >= stamp)
		dt_sec = (float)(_stamp - stamp) * 0.000001f;
	if (dt_sec < 0.0f) dt_sec = 0.0f;
	if (dt_sec > 0.25f) dt_sec = 0.25f;
	if (torque_handled != 2 && fabsf(io->torque) > 0.001f)
	{
		const float mp_yaw_rate_deg_per_sec = 180.0f;
		io->yaw += io->torque * mp_yaw_rate_deg_per_sec * dt_sec;
		// FL-1733: mark that Q/E torque is active so snapshot yaw resync is suppressed.
		// At 180°/s turn rate, any RTT > 28ms causes divergence > 5° threshold, making
		// the resync snap fire every snapshot and producing visible camera jitter.
		// LINEAGE_JSON: {"fl":"FL-1733","cautionary_precedent":"yaw_resync_not_lag_fix","note":"DO NOT reinvest in yaw resync as a lag/movement fix. This is camera-only."}
		player.last_torque_active_stamp = _stamp;
	}
	while (io->yaw > 180.0f) io->yaw -= 360.0f;
	while (io->yaw < -180.0f) io->yaw += 360.0f;

	debug.dbg_io_x_force_applied = io->x_force;
	debug.dbg_io_y_force_applied = io->y_force;
	debug.dbg_io_z_force_applied = io->z_force;
	debug.dbg_io_fly_applied = io->fly ? 1 : 0;

	if (is_server_session)
	{
		io->torque = 1000000.0f;
		player.yaw_vel = 0.0f;
	}

	{
		auto snap_move = [](float v) -> float {
			if (!isfinite(v)) return 0.0f;
			if (v > 1.0f) v = 1.0f;
			if (v < -1.0f) v = -1.0f;
			return (float)((int8_t)(int)roundf(v * 127.0f)) / 127.0f;
		};
		auto snap_yaw = [](float yaw) -> float {
			if (!isfinite(yaw)) return 0.0f;
			while (yaw > 180.0f) yaw -= 360.0f;
			while (yaw < -180.0f) yaw += 360.0f;
			return (float)((int16_t)(int)roundf(yaw * 100.0f)) / 100.0f;
		};
		io->x_force = snap_move(io->x_force);
		io->y_force = snap_move(io->y_force);
		io->z_force = snap_move(io->z_force);
		io->yaw = snap_yaw(io->yaw);
	}

	debug.dbg_input_world_dir = -1.0f;
	{
		float input_len = sqrtf(io->x_force * io->x_force + io->y_force * io->y_force);
		if (input_len > 0.01f)
		{
			float dx = io->x_force / input_len;
			float dy = io->y_force / input_len;
			const float yaw_rad = (float)(io->yaw * (M_PI / 180.0f));
			float world_dx = dx * cosf(yaw_rad) - dy * sinf(yaw_rad);
			float world_dy = dx * sinf(yaw_rad) + dy * cosf(yaw_rad);
			float desired_dir = (float)(atan2(world_dx, world_dy) * 180.0f / M_PI);
			while (desired_dir < 0.0f) desired_dir += 360.0f;
			while (desired_dir >= 360.0f) desired_dir -= 360.0f;
			debug.dbg_input_world_dir = desired_dir;
		}
	}
}

void PrepareLocalMovementStepIO(InputState& input, LocalPlayerState& player,
	CameraState& camera, const GameSession& session, const UiState& ui,
	DebugTelemetryState& debug, bool authoritative_session, bool is_server_session,
	int server_local_id, uint64_t _stamp, uint64_t stamp, Physics* physics, PhysicsIO* io)
{
	if (!io)
		return;

	*io = {};
	io->x_force = 0;
	io->y_force = 0;
	io->torque = 0;

	// Phase 1: Read pose from physics or fall back to player state
	ReadPoseOrFallback(physics, player, authoritative_session,
		(float)session.water, io);

	// Phase 2: Apply touch-contact forces (FORCE / TORQUE)
	ContactForceResult contact = ApplyContactForces(
		input, camera, session, player, physics,
		is_server_session, _stamp, stamp, io);

	// Phase 3: Apply keyboard forces (WASD/arrows, torque keys, fly)
	ApplyKeyboardForces(input, player, session, ui, debug, camera,
		contact.force_handled, contact.torque_handled,
		server_local_id, is_server_session,
		_stamp, stamp, io);

	// Phase 4: Merge gamepad axis into forces / torque
	MergeGamepadInput(input.pad_axis, ui.menu_depth, io);

	// Phase 5: Finalize — API merge, mount clamp, yaw integration, snap
	FinalizeIO(input, player, debug, _stamp, stamp,
		is_server_session, contact.torque_handled, io);
}

MpMoveSendLifecycleResult SendLocalNetworkUpdates(
	MpMoveState& mp_move,
	Server* server,
	uint64_t stamp,
	const PhysicsIO& io,
	Terrain* terrain,
	World* world,
	float water)
{
	// FL-2957 TRACE: 'M' movement path step 2 — this is the client outflow seam.
	// PhysicsIO with x_force/y_force built by PrepareLocalMovementStepIO
	// (engine/game_render_bridge.cpp:390) is serialized and sent to the server
	// via server->Send(). The server leg is SvrProcessInputMove in
	// server/server_tick.cpp:3612, which writes into ps->latest_input.
	// There is no explicit zeroing on this path — forces survive from here
	// into the tick loop unless SVR_INPUT_STALE_TICKS expires.
	// Part of Attempt #23: source trace overlays (23 attempts, 0 closed).
	if (server && server->authority.snapshot_client.snapshot_packets == 0)
	{
		static int fl2896_wait_for_snapshot_logs = 0;
		if (fl2896_wait_for_snapshot_logs < 16)
		{
			printf("[FL-2896-DEFER-LOCAL-UPDATES] stamp=%llu local_id=%d snapshots=%u\n",
				(unsigned long long)stamp,
				server->connection.local_id,
				(unsigned)server->authority.snapshot_client.snapshot_packets);
			fflush(stdout);
			fl2896_wait_for_snapshot_logs++;
		}
		return {};
	}
	// FL-4137 #25: Gap D client-side placed-block shadow collider build
	// DELETED. Placed-block collision is server-owned world entity data; this
	// client diagnostic must not recreate local placed-block collision.
	return MpMoveRunSendLifecycle(
		&mp_move,
		server,
		stamp,
		&io,
		false,
		MOUNT::NONE,
		terrain,
		world,
		water);
}

void ApplyLocalInputYawRelease(Physics* physics, float prev_yaw, float yaw_vel)
{
	if (!physics)
		return;
	PhysicsTeleportCommand command = {};
	command.set_yaw = true;
	command.yaw = prev_yaw;
	command.yaw_vel = yaw_vel;
	PhysicsTeleport(physics, command);
}

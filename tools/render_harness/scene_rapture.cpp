// scene_rapture.cpp — UFO Rapture Death (docs/specs/2026-10-08-ufo-rapture-death.md)
//
// Timeline, one clip frame per 30 Hz tick (fps must be 30):
//   frames [0, lead)                 : ALIVE, body sheet idle (anim 0, frame 0)
//   frames [lead, lead + 180)        : DEAD, death sheet; ticks_since_death 0..179
//   frames [lead + 180, ...)         : ALIVE again at the spawn point (respawn
//                                      at 2 x SVR_RESPAWN_DELAY_TICKS = 180)
// The camera yaw turns continuously by `yaw_sweep` degrees over the whole
// clip (turntable style); pitch and zoom are the game defaults.
//
// WHAT IS REAL ENGINE / FEATURE CODE
//   - RaptureBodyZ (engine/rapture_timeline.h): the harness stands in for the
//     server (SvrProcessRapture) and writes the body Z with the same call.
//   - Death clip frame: ComputeFrameByPlayback with the playback mode and
//     steady frame of the compiled kind-602 row (plydie_0000_p602...), i.e.
//     what ResolveActorVisualProfile does, one frame per tick.
//   - Body visibility / dissolve: Renderer::RenderSprite's rapture branch,
//     reached because the actor Inst carries a Character (life_state DEAD,
//     presentation_started_tick = death tick) that the RenderFrameInput names
//     as remote.local_player, with authoritative_tick = current tick.
//   - UFO placement and beam: RenderRaptureUfos / RenderRaptureBeams in
//     render_scene.cpp, fed by RenderAppendRaptureAnchor
//     (engine/render/render_rapture_anchor.h), the same helper
//     game_render_bridge.cpp calls for the local player
//     (sample pose = body pos at auth_tick). ufo.xp is loaded with the same
//     LoadSprite call SpriteRegistry::LoadSpriteBP makes.
//
// WHAT THE HARNESS STANDS IN FOR
//   - the server (tick counter, death_tick, body Z write, respawn at 180);
//   - the actor visual profile resolver (the death/body sheets are set on the
//     Inst directly; the 2026-10-08-* skins have no profile rows yet);
//   - the camera: with camera_follow=1 (default) it follows the actor like the
//     game's local-player camera: cam = (body x, y, smoothed body z), using
//     the same smoothing rule as game_render_bridge.cpp ("Smooth camera Z":
//     halve the residual each frame, snap when |dz| > 5), stepped once per
//     30 fps frame. The blob shadow (RenderStageShadow) is keyed to the
//     camera pos exactly as in game. camera_follow=0 pins the camera at the
//     death ground (a bystander's framing, but the shadow then stays behind).
//   - no_ufo=1 leaves RenderFrameInput::ufo_sprite null: the per-run stand-in
//     for building with RAPTURE_UFO_VISIBLE 0 (no saucer, and RenderRaptureBeams
//     drops the hull shadow because no UFO sprite is drawn). Used for
//     all-rapture-no-ufo.gif and to measure the UFO's top margin by diffing
//     against a normal run.

#include "harness.h"
#include "sprite.h"
#include "world.h"
#include "character.h"
#include "render_frame_input.h"
#include "render_rapture_anchor.h"
#include "rapture_timeline.h"
#include "actor_visual_profile_runtime.h"
#include "../server/protocol/protocol_common.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

namespace
{
const uint32_t kRespawnTicks = 2u * 90u; // 2 x SVR_RESPAWN_DELAY_TICKS (server_state.h)
const uint32_t kTickBase = 1000u;        // authoritative ticks are never 0

struct RaptureState
{
	Sprite* body = nullptr;
	Sprite* death = nullptr;
	Sprite* ufo = nullptr;
	float yaw0 = -57.4f;
	float yaw_sweep = 180.0f;
	float actor_dir = 0.0f;
	int lead = 15;
	bool camera_follow = true;
	bool no_ufo = false;
	bool cam_smooth_init = false;
	float cam_smooth_z = 0.0f;
	float death_pos[3] = { 0, 0, 0 };   // death_ground_z in [2]
	float spawn_pos[3] = { 0, 0, 0 };
	uint8_t death_mode = ACTOR_VISUAL_PLAYBACK_DIRECTION_FORWARD_CLAMP;
	uint32_t death_steady = 0;
	Character character;
	RenderFrameInput fi;
};
RaptureState g_rp;
}

static bool RaptureSetup(Harness* h, const HarnessArgs& a)
{
	if (!h->LoadMap(HarnessArg(a, "map", "assets/a3d/preview_flat_green.a3d")))
		return false;
	std::string body = HarnessArg(a, "body", "");
	std::string death = HarnessArg(a, "death", "");
	if (body.empty() || death.empty())
	{
		fprintf(stderr, "[rapture] body=<idle .xp> and death=<plydie .xp> are required\n");
		return false;
	}
	if (h->fps != (int)RAPTURE_TICK_RATE)
		fprintf(stderr, "[rapture] warning: fps=%d, the clip assumes one frame per 30 Hz tick\n", h->fps);
	g_rp.body = h->LoadSpriteXp(body, HarnessArgI(a, "layer", 2));
	g_rp.death = h->LoadSpriteXp(death, HarnessArgI(a, "layer", 2));
	// SpriteRegistry::LoadSprites: ufo_sprite = LoadSpriteBP("ufo.xp", nullptr, false)
	std::string ufo_path = h->Path("assets/sprites/ufo.xp");
	g_rp.ufo = LoadSprite(ufo_path.c_str(), "ufo.xp", nullptr, false);
	if (!g_rp.body || !g_rp.death || !g_rp.ufo)
	{
		fprintf(stderr, "[rapture] sprite load failed\n");
		return false;
	}

	// Death clip playback metadata from the compiled kind-602 profile row.
	const char* row_id = "plydie_0000_p602_m0_h0_c0_w0_s0";
	bool found = false;
	for (int i = 0; i < kCompiledActorVisualRowCount; i++)
	{
		if (!strcmp(kCompiledActorVisualRows[i].id, row_id))
		{
			g_rp.death_mode = kCompiledActorVisualRows[i].playback_mode;
			g_rp.death_steady = kCompiledActorVisualRows[i].steady_frame_index;
			found = true;
			break;
		}
	}
	if (!found)
	{
		fprintf(stderr, "[rapture] compiled row %s not found\n", row_id);
		return false;
	}
	fprintf(stderr, "[rapture] death playback mode=%d steady=%u (row %s), clip len=%d\n",
		g_rp.death_mode, g_rp.death_steady, row_id, g_rp.death->anim[0].length);

	g_rp.yaw0 = HarnessArgF(a, "yaw", h->camera.yaw);
	g_rp.yaw_sweep = HarnessArgF(a, "yaw_sweep", 180.0f);
	g_rp.actor_dir = HarnessArgF(a, "dir", g_rp.yaw0);
	g_rp.lead = HarnessArgI(a, "lead", 15);
	g_rp.camera_follow = HarnessArgI(a, "camera_follow", 1) != 0;
	g_rp.no_ufo = HarnessArgI(a, "no_ufo", 0) != 0;

	g_rp.death_pos[0] = HarnessArgF(a, "x", h->camera.pos[0]);
	g_rp.death_pos[1] = HarnessArgF(a, "y", h->camera.pos[1]);
	g_rp.death_pos[2] = HarnessArgF(a, "z", h->GroundZ(g_rp.death_pos[0], g_rp.death_pos[1]));
	// The player died at the map spawn, so the respawn lands on the same spot.
	memcpy(g_rp.spawn_pos, g_rp.death_pos, sizeof(g_rp.spawn_pos));

	h->AddActor("actor", g_rp.body, g_rp.death_pos, g_rp.actor_dir, 0, 0);
	HarnessActor* act = h->Actor("actor");

	memset(&g_rp.character, 0, sizeof(g_rp.character));
	g_rp.character.life_state = LIFE_STATE::ALIVE;
	g_rp.character.HP = g_rp.character.MAX_HP = 100;
	// game.cpp EnsureLocalPlayerInst: SetInstSpriteData(player_inst, &player)
	SetInstSpriteData(act->inst, &g_rp.character);

	memset(&g_rp.fi, 0, sizeof(g_rp.fi));
	h->frame_input = &g_rp.fi;

	h->camera.pos[0] = g_rp.death_pos[0];
	h->camera.pos[1] = g_rp.death_pos[1];
	h->camera.pos[2] = g_rp.death_pos[2];
	h->camera.yaw = g_rp.yaw0;
	return true;
}

static void RaptureTick(Harness* h, int tick)
{
	HarnessActor* a = h->Actor("actor");
	const float t = h->frames > 1 ? (float)tick / (float)(h->frames - 1) : 0.0f;
	h->camera.yaw = g_rp.yaw0 + g_rp.yaw_sweep * t;

	// "server" state for this tick
	const uint32_t auth_tick = kTickBase + (uint32_t)tick;
	const uint32_t death_tick = kTickBase + (uint32_t)g_rp.lead;
	const bool dead = tick >= g_rp.lead && auth_tick - death_tick < kRespawnTicks;
	Character& c = g_rp.character;

	a->dir = g_rp.actor_dir;
	if (dead)
	{
		const uint32_t ticks = RaptureTicksSince(auth_tick, death_tick);
		c.life_state = LIFE_STATE::DEAD;
		c.HP = 0;
		c.presentation_started_tick = death_tick;
		// SvrProcessRapture: ps->pos[2] = RaptureBodyZ(ps->death_ground_z, elapsed)
		a->pos[0] = g_rp.death_pos[0];
		a->pos[1] = g_rp.death_pos[1];
		a->pos[2] = RaptureBodyZ(g_rp.death_pos[2], ticks);
		a->sprite = g_rp.death;
		a->anim = 0;
		a->frame = (int)ComputeFrameByPlayback(g_rp.death_mode, ticks,
			(uint32_t)g_rp.death->anim[0].length, g_rp.death_steady);
	}
	else
	{
		const bool respawned = tick >= g_rp.lead;
		c.life_state = LIFE_STATE::ALIVE;
		c.HP = c.MAX_HP;
		c.presentation_started_tick = respawned ? death_tick + kRespawnTicks : 0;
		const float* p = respawned ? g_rp.spawn_pos : g_rp.death_pos;
		a->pos[0] = p[0];
		a->pos[1] = p[1];
		a->pos[2] = p[2];
		a->sprite = g_rp.body;
		a->anim = 0;
		a->frame = 0;
	}
	c.pos[0] = a->pos[0];
	c.pos[1] = a->pos[1];
	c.pos[2] = a->pos[2];
	c.dir = a->dir;

	if (g_rp.camera_follow)
	{
		// game_render_bridge.cpp: cam_pos = { x, y, cam_smooth_z }
		if (!g_rp.cam_smooth_init)
		{
			g_rp.cam_smooth_z = a->pos[2];
			g_rp.cam_smooth_init = true;
		}
		const float dz = a->pos[2] - g_rp.cam_smooth_z;
		if (dz > RAPTURE_CAMERA_Z_SNAP_THRESHOLD || dz < -RAPTURE_CAMERA_Z_SNAP_THRESHOLD)
			g_rp.cam_smooth_z = a->pos[2]; // teleport/respawn snap
		else
			g_rp.cam_smooth_z += dz * 0.5f;
		h->camera.pos[0] = a->pos[0];
		h->camera.pos[1] = a->pos[1];
		h->camera.pos[2] = g_rp.cam_smooth_z;
	}

	// RenderFrameInput, as game_render_bridge.cpp fills it for the local player.
	RenderFrameInput& fi = g_rp.fi;
	memset(&fi, 0, sizeof(fi));
	fi.valid = true;
	fi.tracking.tracked_remote_pid = -1;
	fi.tracking.tracked_npc_entity_id = -1;
	fi.remote.local_player = &c;
	fi.authoritative_tick = auth_tick;
	fi.ufo_sprite = g_rp.no_ufo ? nullptr : g_rp.ufo;
	if (c.life_state == LIFE_STATE::DEAD)
		RenderAppendRaptureAnchor(&fi, c.presentation_started_tick, c.pos, auth_tick, auth_tick);
}

HARNESS_REGISTER_SCENE(rapture,
	"rapture",
	"body=<idle xp> death=<plydie xp> [map=] [layer=2] [yaw=-57.4] [yaw_sweep=180] [dir=yaw] [lead=15] [camera_follow=1] [no_ufo=0] [x= y= z=]",
	RaptureSetup,
	RaptureTick,
	nullptr);

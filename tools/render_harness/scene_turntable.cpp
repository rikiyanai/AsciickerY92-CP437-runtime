// scene_turntable.cpp — camera turntable around one stationary body sheet.
//
// Timeline (frames = fps * seconds):
//   first `idle_fraction` of the clip : anim 0 (idle), frame 0
//   remainder                         : anim 1 (walk), walking in place
// The camera yaw turns continuously by `yaw_sweep` degrees over the whole
// clip and keeps turning across the idle->walk switch.
//
// Sprite direction is chosen by the engine (RenderSprite ->
// FacingSpriteAngleIndex(actor_dir, camera_yaw, angles)). A 180-degree camera
// sweep alone only crosses 5 of the 8 sprite rows, so by default the actor
// also turns in place (`rel_sweep`, the total change of actor-dir minus
// camera-yaw) so every one of the 8 rows is shown in the idle half AND again
// in the walk half. Pass `actor_spin=0` to keep the actor's facing fixed and
// get a pure camera orbit (only 5 rows over 180 degrees).
//
// Walk cadence: actor visual profile rows use LOOP playback with
// frame = elapsed_authoritative_ticks % anim_len (actor_visual_profile_runtime.h
// ComputeFrameByPlayback), and authoritative ticks run at SVR_TICK_RATE = 30 Hz
// (server/server_state.h). So the walk advances one sheet frame per 30 Hz tick.

#include "harness.h"
#include "sprite.h"
#include <math.h>
#include <stdio.h>

static const int kServerTickRate = 30; // server/server_state.h SVR_TICK_RATE

namespace
{
struct TurntableState
{
	float yaw0 = -57.4f;
	float yaw_sweep = 180.0f;
	float rel_sweep = 720.0f;
	float idle_fraction = 0.5f;
	int walk_anim = 1;
	int idle_anim = 0;
	bool have_actor_spin = false;
	float actor_spin = 0.0f;
};
TurntableState g_tt;
}

static bool TurntableSetup(Harness* h, const HarnessArgs& a)
{
	if (!h->LoadMap(HarnessArg(a, "map", "assets/a3d/preview_flat_green.a3d")))
		return false;
	std::string sprite = HarnessArg(a, "sprite", "");
	if (sprite.empty())
	{
		fprintf(stderr, "[turntable] sprite=<path to body .xp> is required\n");
		return false;
	}
	Sprite* s = h->LoadSpriteXp(sprite, HarnessArgI(a, "layer", 2));
	if (!s)
		return false;
	if (s->angles != 8 || s->anims < 2)
		fprintf(stderr, "[turntable] warning: expected 8 angles and idle+walk anims, got %d/%d\n", s->angles, s->anims);

	g_tt.yaw0 = HarnessArgF(a, "yaw", h->camera.yaw);
	g_tt.yaw_sweep = HarnessArgF(a, "yaw_sweep", 180.0f);
	g_tt.rel_sweep = HarnessArgF(a, "rel_sweep", 720.0f);
	g_tt.idle_fraction = HarnessArgF(a, "idle_fraction", 0.5f);
	g_tt.have_actor_spin = a.count("actor_spin") > 0;
	g_tt.actor_spin = HarnessArgF(a, "actor_spin", 0.0f);

	float pos[3] = { HarnessArgF(a, "x", h->camera.pos[0]), HarnessArgF(a, "y", h->camera.pos[1]), 0 };
	pos[2] = HarnessArgF(a, "z", h->GroundZ(pos[0], pos[1]));
	h->AddActor("actor", s, pos, g_tt.yaw0, g_tt.idle_anim, 0);

	// Game camera follows the player: cam_pos = (player x, y, smoothed z).
	h->camera.pos[0] = pos[0];
	h->camera.pos[1] = pos[1];
	h->camera.pos[2] = pos[2];
	h->camera.yaw = g_tt.yaw0;
	return true;
}

static void TurntableTick(Harness* h, int tick)
{
	HarnessActor* a = h->Actor("actor");
	const float t = h->frames > 1 ? (float)tick / (float)h->frames : 0.0f;

	h->camera.yaw = g_tt.yaw0 + g_tt.yaw_sweep * t;

	// Relative facing (actor dir - camera yaw). Start half a sector early so
	// every sprite row gets an equal-length band.
	const float sector = 360.0f / (float)(a->sprite->angles > 0 ? a->sprite->angles : 8);
	float rel;
	if (g_tt.have_actor_spin)
		rel = (g_tt.yaw0 + g_tt.actor_spin * t) - h->camera.yaw;
	else
		rel = -0.5f * sector + 0.01f + g_tt.rel_sweep * t;
	a->dir = h->camera.yaw + rel;
	while (a->dir > 180.0f) a->dir -= 360.0f;
	while (a->dir < -180.0f) a->dir += 360.0f;

	const int idle_frames = (int)floorf(h->frames * g_tt.idle_fraction + 0.5f);
	if (tick < idle_frames)
	{
		a->anim = g_tt.idle_anim;
		a->frame = 0;
	}
	else
	{
		a->anim = g_tt.walk_anim;
		int walk_ticks = (tick - idle_frames) * kServerTickRate / (h->fps > 0 ? h->fps : 30);
		int len = a->sprite->anim[a->anim].length;
		a->frame = len > 0 ? walk_ticks % len : 0;
	}
}

HARNESS_REGISTER_SCENE(turntable,
	"turntable",
	"sprite=<xp> [map=] [layer=2] [yaw=-57.4] [yaw_sweep=180] [rel_sweep=720 | actor_spin=deg] [idle_fraction=0.5] [x= y= z=]",
	TurntableSetup,
	TurntableTick,
	nullptr);

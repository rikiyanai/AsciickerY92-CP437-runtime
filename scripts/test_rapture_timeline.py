#!/usr/bin/env python3
"""Compile and run the RaptureEval unit test (engine/rapture_timeline.h).

Also checks that the server and client actually include the shared header,
so the test exercises the same code both sides run.
"""

from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
TEST_SRC = ROOT / "scripts" / "test_rapture_timeline.cpp"
HEADER = ROOT / "engine" / "rapture_timeline.h"
INCLUDERS = [
    ROOT / "server" / "server_tick.cpp",
    ROOT / "engine" / "render" / "render_sprite_blit.cpp",
]


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


if not HEADER.is_file():
    fail(f"missing {HEADER.relative_to(ROOT)}")
for path in INCLUDERS:
    if not path.is_file():
        fail(f"missing {path.relative_to(ROOT)}")
    if "rapture_timeline.h" not in path.read_text():
        fail(f"{path.relative_to(ROOT)} does not include rapture_timeline.h")

# Server wiring contract (source-level; no headless kill harness exists yet).
server_src = (ROOT / "server" / "server_tick.cpp").read_text()
if server_src.count("SvrApplyPlayerDeathBookkeeping(state, (int)hit") != 2:
    fail("melee and projectile HP<=0 sites must both call SvrApplyPlayerDeathBookkeeping")
book = server_src.split("static void SvrApplyPlayerDeathBookkeeping(", 1)[1].split("\n}\n", 1)[0]
if "collision_debug_sample_count = 0;" not in book:
    fail("death bookkeeping must clear the body's stale collision debug samples (they flooded the client queue)")
if "SvrProcessRespawns(state);\n    SvrProcessRapture(state);" not in server_src:
    fail("SvrProcessRapture must run right after SvrProcessRespawns in the tick")
req = server_src.split("static void SvrProcessRespawnReq(", 1)[1].split("\n}\n", 1)[0]
if "SvrTransitionClientPhase" in req or "respawn_tick" in req:
    fail("SvrProcessRespawnReq must not change phase: 'R' is disabled (respawn is fixed at 180 ticks)")
if "CPHASE_ALIVE" not in req or "respawn_req_disabled_while_not_alive" not in req:
    fail("SvrProcessRespawnReq must reject non-alive players with a logged reason")
rapture = server_src.split("static void SvrProcessRapture(", 1)[1].split("\n}\n", 1)[0]
if "RaptureBodyZ(ps->death_ground_z" not in rapture:
    fail("SvrProcessRapture must drive pos[2] through RaptureBodyZ")
if "SVR_RESPAWN_DELAY_TICKS    90" not in (ROOT / "server" / "server_state.h").read_text():
    fail("respawn delay changed; rapture assumes death->respawn = 180 ticks")

# Client contract: visibility/dissolve in the actor queue, never a render-side
# Z lift (FL-1434/FL-2023: server owns actor Z).
blit_src = (ROOT / "engine" / "render" / "render_sprite_blit.cpp").read_text()
if "rapture.body_visible" not in blit_src or "rapture_dissolve" not in blit_src:
    fail("actor queue must skip/dissolve DEAD players via RaptureEval")
for path in sorted((ROOT / "engine" / "render").glob("*.cpp")) + [ROOT / "engine" / "game_render_bridge.cpp"]:
    if "body_z_offset" in path.read_text():
        fail(f"{path.relative_to(ROOT)} uses body_z_offset; the server owns actor Z")
bridge_src = (ROOT / "engine" / "game_render_bridge.cpp").read_text()
if "if (dz > 5.0f || dz < -5.0f) // teleport/respawn snap" not in bridge_src:
    fail("camera Z snap threshold changed; update RAPTURE_CAMERA_Z_SNAP_THRESHOLD and re-check the lift")
if "render_fi.authoritative_tick = server->authority.snapshot_client.last_snapshot_tick;" not in bridge_src:
    fail("render frame input must carry the authoritative tick for RaptureEval")

# Offline rapture is out of scope for v1 (spec section 6 step 6, deferred):
# offline has no respawn, so nothing offline may hide or lift the body.
if "render_fi.valid = (server != nullptr);" not in bridge_src:
    fail("render frame input must be invalid offline so the rapture body cut-over cannot run")
if "if (server && render_fi.authoritative_tick != 0)" not in bridge_src:
    fail("rapture UFO/beam anchors must be server-only")
for path in [ROOT / "engine" / "game_render_bridge.cpp", ROOT / "engine" / "game_input.cpp",
             ROOT / "engine" / "render" / "render_sprite_blit.cpp", ROOT / "engine" / "render" / "render_frame_input.h"]:
    if "offline_rapture" in path.read_text():
        fail(f"{path.relative_to(ROOT)} has an offline rapture path; offline is deferred")

# Step 4: UFO asset + render-only placement contract.
sys.path.insert(0, str(ROOT / "scripts" / "pipeline"))
from xp_core import XPFile  # noqa: E402

UFO = ROOT / "assets" / "sprites" / "ufo.xp"
if not UFO.is_file():
    fail("missing assets/sprites/ufo.xp (scripts/adhoc/2026-10-08-make-ufo-sprite.py)")
import contextlib, io  # noqa: E401,E402
with contextlib.redirect_stdout(io.StringIO()):
    ufo = XPFile(str(UFO))
meta = ufo.get_metadata()
if meta.get("angles") != 1 or meta.get("projs") != 1 or meta.get("anims") != [4]:
    fail(f"ufo.xp layout must be 1 angle, no reflection, 4 frames; got {meta}")
KEY, MAGENTA, CYAN = (255, 255, 85), (255, 0, 255), (0, 255, 255)
key_q = tuple((v * 5 + 128) // 255 for v in KEY)
for y, row in enumerate(ufo.layers[2].data):
    for x, (g, fg, bg) in enumerate(row):
        if tuple(bg) == MAGENTA:
            continue
        for c in (tuple(fg), tuple(bg)):
            if c in (KEY, CYAN):
                continue
            if tuple((v * 5 + 128) // 255 for v in c) == key_q:
                fail(f"ufo.xp cell ({x},{y}) colour {c} quantizes to the transparency key")
        if tuple(fg) == KEY:
            fail(f"ufo.xp cell ({x},{y}): visible fg equals the transparency key")
        if CYAN in (tuple(fg), tuple(bg)):
            fail(f"ufo.xp cell ({x},{y}): no cyan swoosh cells (the beam is yellow)")
timeline_src = HEADER.read_text()
if f"#define RAPTURE_UFO_SHEET_ROWS              {len(ufo.layers[2].data)}" not in timeline_src:
    fail("RAPTURE_UFO_SHEET_ROWS must match the ufo.xp sheet height")
if "#define RAPTURE_UFO_VISIBLE                 1" not in timeline_src:
    fail("RAPTURE_UFO_VISIBLE must default to 1 (visible)")
if "RENDER_MAX_RAPTURES = 16" not in (ROOT / "engine" / "render" / "render_frame_input.h").read_text():
    fail("beam cell budget in test_rapture_beam.cpp assumes RENDER_MAX_RAPTURES = 16")
if "LoadSpriteBP(\"ufo.xp\"" not in (ROOT / "engine" / "sprite_registry.cpp").read_text():
    fail("SpriteRegistry must load ufo.xp")
scene_src = (ROOT / "engine" / "render" / "render_scene.cpp").read_text()
ufo_pass = scene_src.split("static void RenderRaptureUfos(", 1)
if len(ufo_pass) != 2 or "r->RenderSprite(ptr, width, height, ufo" not in ufo_pass[1].split("\n}\n", 1)[0]:
    fail("RenderRaptureUfos must draw the UFO through Renderer::RenderSprite")
# Render-only: the UFO sheet is never handed to a world Inst constructor.
for path in sorted((ROOT / "engine").rglob("*.cpp")):
    for line in path.read_text(errors="ignore").splitlines():
        if "ufo_sprite" in line and "Inst(" in line:
            fail(f"{path.relative_to(ROOT)}: the UFO must not become a world Inst: {line.strip()}")
if "render_fi.ufo_sprite = SpriteRegistry::ufo_sprite;" not in bridge_src:
    fail("render frame input must carry the UFO sprite")

cxx = os.environ.get("CXX") or shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")
if not cxx:
    fail("no C++ compiler found (set CXX)")

# Step 5: beam overlay contract (shape itself is unit tested below).
beam_pass = scene_src.split("static void RenderRaptureBeams(", 1)
if len(beam_pass) != 2:
    fail("render_scene.cpp must define RenderRaptureBeams")
beam_body = beam_pass[1].split("\n}\n", 1)[0]
for needle in ("RaptureBeamCellAt(", "DepthTest_RO(", "RaptureBeamShade(", "rr->ticks_since_death"):
    if needle not in beam_body:
        fail(f"RenderRaptureBeams must use {needle}")
if "RAPTURE_UFO_VISIBLE" not in scene_src.split("static void RenderRaptureUfos(", 1)[1].split("\n}\n", 1)[0]:
    fail("RenderRaptureUfos must honour RAPTURE_UFO_VISIBLE")
if "->height =" in beam_body or "->height=" in beam_body:
    fail("RenderRaptureBeams must not write sample depth (swoosh-style overlay)")
if "RenderRaptureBeams(r, out_ptr, width, height);\n\tRenderRaptureUfos(r, out_ptr, width, height);" not in scene_src:
    fail("beams must be drawn right before the UFO, after the actor blit")

# Fix 1 (2026-10-08): the local camera stays on the death ground during the
# rapture window, and the local yaw is not resynced to the frozen death yaw.
if "RaptureCameraFollowZ(local_display_pos[2], local_in_rapture_window," not in bridge_src:
    fail("the local camera must follow RaptureCameraFollowZ (death ground Z) in the rapture window")
cam_block = bridge_src.split("float cam_follow_z = local_display_pos[2];", 1)
if len(cam_block) != 2 or "float cam_pos[3] = { local_display_pos[0], local_display_pos[1], camera.cam_smooth_z };" not in cam_block[1].split("auto publish_authoritative_web_core_render_probe", 1)[0]:
    fail("camera XY must still follow the display pos; only the smoothed Z input changes")
if "float dz = cam_follow_z - camera.cam_smooth_z;" not in bridge_src:
    fail("the camera Z smoother must consume cam_follow_z")
ingest_src = (ROOT / "engine" / "network_ingest_snapshot.cpp").read_text()
if "if (!torque_recently_active && !local_dead)" not in ingest_src:
    fail("local yaw resync must stay off while dead (the server yaw is frozen at the death yaw)")

# Fix 2 (2026-10-08): the companion steps back out of the beam footprint.
if "float scale = 3.0;" not in scene_src or "rapture footprint columns-per-world-unit mirror is stale" not in scene_src:
    fail("render_scene.cpp must keep the DBL scale 3 the footprint mirror static_asserts against")
if '#include "../engine/rapture_standoff.h"' not in server_src:
    fail("server_tick.cpp must include rapture_standoff.h")
ai = server_src.split("static void SvrUpdateNpcAI(", 1)[1].split("\n}\n", 1)[0]
for needle in ("RaptureCompanionStandoffStep(", "npc->follow_active = step.move;"):
    if needle not in ai:
        fail(f"SvrUpdateNpcAI must use {needle}")
if "PhysicsTeleport" in ai.split("if (owner->active && owner->rapture_active)", 1)[1].split("continue;", 1)[0]:
    fail("the companion stand-off must walk, never teleport")
mp_step_src = (ROOT / "server" / "mp_step.cpp").read_text()
for needle in ("move_dx_world = dx * cosf(yaw_rad) - dy * sinf(yaw_rad);",
               "move_dy_world = dx * sinf(yaw_rad) + dy * cosf(yaw_rad);",
               "next.yaw = MpWrapYaw(input.yaw);"):
    if needle not in mp_step_src:
        fail("MpStepOnce force/yaw rotation changed; RaptureNpcIntentFromWorld inverts it")
if "step_input.yaw = npc->intent_dir;" not in server_src:
    fail("NPC physics step yaw must still come from intent_dir (RaptureNpcIntentFromWorld assumes it)")
if "!have_alive_players && !have_rapture_players" not in server_src:
    fail("NPC physics must keep stepping while a player is in the rapture window")

# Fix 3 (2026-10-08): no NPC targets a dead / raptured player, and one inside
# the footprint backs out of it.
for needle in ("RaptureNpcRetreatStep(", "if (!SvrPlayerTargetable(ps))"):
    if needle not in ai:
        fail(f"SvrUpdateNpcAI must use {needle}")
if server_src.count("if (!SvrPlayerTargetable(target))") != 2:
    fail("swing target resolution and swing hit collection must both use SvrPlayerTargetable")
if ai.count("RaptureNpcIntentFromWorld(") != 2:
    fail("companion stand-off and NPC retreat must express their world direction via RaptureNpcIntentFromWorld")

TESTS = [(TEST_SRC, "test_rapture_timeline"),
         (ROOT / "scripts" / "test_rapture_beam.cpp", "test_rapture_beam"),
         (ROOT / "scripts" / "test_rapture_standoff.cpp", "test_rapture_standoff")]
with tempfile.TemporaryDirectory(prefix="rapture_timeline_") as tmp:
    for src, name in TESTS:
        exe = Path(tmp) / name
        cmd = [cxx, "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
               "-I", str(ROOT / "engine"), str(src), "-o", str(exe)]
        build = subprocess.run(cmd, capture_output=True, text=True)
        if build.returncode != 0:
            sys.stdout.write(build.stdout)
            sys.stderr.write(build.stderr)
            fail(f"{src.name} did not compile")
        run = subprocess.run([str(exe)], capture_output=True, text=True)
        sys.stdout.write(run.stdout)
        sys.stderr.write(run.stderr)
        if run.returncode != 0:
            fail(f"{name} reported failures")

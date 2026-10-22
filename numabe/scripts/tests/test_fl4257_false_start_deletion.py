#!/usr/bin/env python3
"""FL-4257 corrective (2026-06-11) — deletion gate for rejected false-start owners.

Two generations of FL-4257 false starts are permanently banned here:

1. The Phase 1 "sinc warp" lane (rejected 2026-06-11): a render-owned
   sinc(arc) screen warp around the unchanged Euclidean divide
   (render_projection_spherical.cpp) plus a server shim that rescaled the
   flat per-tick dx/dy before the old flat collision ran
   (mp_step_curved.cpp). Neither changed the geometry model; both owned
   curvature scale locally (kSphericalSceneScale) instead of consuming
   server-owned S3 state. The ASCIICKER_TEST_MODE_SKIP_AUTH preview hatch
   let a client run without server authority — same ownership violation.

2. The chart-displacement lane (rejected on the old working branch, tip
   9bb65ac): chart->S3->chart displacement of projected vertices, a
   render-generated water shell pretending to be world closure, and a
   far-ocean resolve fill. Banned pre-emptively so the rebuild cannot
   resurrect them under the old names.

The replacement architecture (single 4D spherical owner, server-owned S3
pose, water as world-model geometry, mandatory dual image) must NOT reuse
any banned symbol. Comments are stripped before scanning so deletion notes
that mention the old names historically do not trip the gate.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

DELETED_FILES = [
    # sinc-warp lane (was on main until 2026-06-11)
    "engine/render/render_projection_spherical.cpp",
    "engine/render/render_projection_spherical.h",
    "server/mp_step_curved.cpp",
    # chart-displacement lane (old branch, never on main — kept banned)
    "engine/render/render_projection_curved.cpp",
    "engine/render/render_projection_curved.h",
    "server/mp_step_geodesic.cpp",
    "godot_project/shaders/fl4257_curved_view.glsl",
]

BANNED_SYMBOLS = [
    # sinc-warp lane
    "kSphericalSceneScale",
    "kServerSphericalSceneScale",
    "MpStepApplySphericalDeltaCorrection",
    "ProjectCoordsSpherical",
    "UnprojectCoords2DSpherical",
    "UnprojectCoords3DSpherical",
    "ProjectWorldVertexPatchPerspSpherical",
    "ProjectWorldVertexSpritePerspSpherical",
    "ProjectFaceVertexPerspSpherical",
    "TransformWorldPointSpherical",
    "ASCIICKER_TEST_MODE_SKIP_AUTH",
    # chart-displacement lane
    "RenderCurvedDisplaceScaledVertex",
    "RenderCurvedUndisplace",
    "RenderCurvedArcFromCamera",
    "RenderCurvedPreDisplace",
    "MpStepGeodesicAdvance",
    "CurvedChartGeodesicAdvance",
    "CurvedInverseLogMapToChart",
    "CurvedChartAntipode",
    "CurvedLogMapWithTransportedFrame",
    "RenderS3WaterShell",
    "RenderS3RasterWaterTri",  # finite chart-ring water path (rejected 2026-06-11)
    "kFarOcean",
    "curved_patch_tolerance",
]

SCAN_DIRS = ["engine", "server", "editor", "platform"]
BUILD_FILES = [
    "apps/asciicker-presenter/makefile_asciiid", "apps/asciicker-presenter/makefile_asciiid_mac", "apps/game-fl4512/makefile_game",
    "apps/game-fl4512/makefile_game_mac", "makefile_game_term", "makefile_game_term_mac",
    "makefile_server", "build-web.sh",
]


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", "", text)
    return text


def _runtime_sources():
    for d in SCAN_DIRS:
        root = REPO_ROOT / d
        if not root.is_dir():
            continue
        for ext in ("*.cpp", "*.h", "*.c", "*.hpp"):
            yield from root.rglob(ext)


def test_deleted_owner_files_stay_deleted() -> None:
    for rel in DELETED_FILES:
        assert not (REPO_ROOT / rel).exists(), (
            f"{rel} is a rejected FL-4257 false-start owner and must stay "
            f"deleted (Law 1: the 4D spherical owner is the only curvature "
            f"render/movement owner)."
        )


def test_banned_symbols_absent_from_runtime_code() -> None:
    offenders = []
    for path in _runtime_sources():
        text = _strip_comments(path.read_text(errors="replace"))
        for sym in BANNED_SYMBOLS:
            if sym in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {sym}")
    assert not offenders, (
        "rejected FL-4257 owner symbols present in runtime code:\n  "
        + "\n  ".join(offenders)
    )


def test_build_wiring_clean() -> None:
    offenders = []
    for rel in BUILD_FILES:
        path = REPO_ROOT / rel
        if not path.exists():
            continue
        text = path.read_text(errors="replace")
        for needle in ("render_projection_spherical", "mp_step_curved",
                       "render_projection_curved", "mp_step_geodesic"):
            if needle in text:
                offenders.append(f"{rel}: {needle}")
    assert not offenders, (
        "build files still wire deleted FL-4257 owners:\n  " + "\n  ".join(offenders)
    )


def test_s3_gpu_terrain_owner_is_wired_for_native_game() -> None:
    """Normal headed spherical terrain must enter the GL terrain owner before
    the recursive CPU terrain reference path."""
    header = (REPO_ROOT / "engine" / "render" / "render_s3.h").read_text()
    scene = (REPO_ROOT / "engine" / "render" / "render_scene.cpp").read_text()
    gpu = (REPO_ROOT / "engine" / "render" / "render_s3_gpu_terrain.cpp").read_text()
    mac_make = (REPO_ROOT / "apps/game-fl4512/makefile_game_mac").read_text()
    linux_make = (REPO_ROOT / "apps/game-fl4512/makefile_game").read_text()
    assert "RenderS3SubmitTerrainGpu(Renderer* r, Terrain* t)" in header
    assert "RenderS3SubmitTerrainGpu(r, t)" in scene
    assert "s3_cpu_terrain_images = s3_gpu_terrain ? 0 : 2" in scene
    assert "QueryTerrain(t, 0, (double (*)[4])0, 0xFF, S3GpuTerrainPatch" in gpu
    assert "glDrawArrays(GL_TRIANGLES" in gpu
    assert "glReadPixels(0, 0, w, h, GL_RGBA, GL_FLOAT" in gpu
    assert "engine/render/render_s3_gpu_terrain.cpp" in mac_make
    assert "engine/render/render_s3_gpu_terrain.cpp" in linux_make


def test_pure_terminal_links_shared_s3_and_morphology_owners() -> None:
    """PURE_TERM must link the shared owners it calls, never local stubs."""
    for rel in ("makefile_game_term", "makefile_game_term_mac"):
        text = (REPO_ROOT / rel).read_text()
        assert "engine/render/render_s3_gpu_terrain.cpp" in text
        assert "engine/glyph_morphology_metadata.cpp" in text


def test_s3_far_side_orientation_parity_reaches_presentation() -> None:
    """Reverse perspective must also carry far-side orientation parity into
    final presentation for directional sprites and material glyphs."""
    internal = (REPO_ROOT / "engine" / "render" / "render_internal.h").read_text()
    s3 = (REPO_ROOT / "engine" / "render" / "render_s3.cpp").read_text()
    gpu = (REPO_ROOT / "engine" / "render" / "render_s3_gpu_terrain.cpp").read_text()
    world = (REPO_ROOT / "engine" / "render" / "render_world_pass.cpp").read_text()
    sprites = (REPO_ROOT / "engine" / "render" / "render_sprite_blit.cpp").read_text()
    resolve = (REPO_ROOT / "engine" / "render" / "render_resolve.cpp").read_text()
    scene = (REPO_ROOT / "engine" / "render" / "render_scene.cpp").read_text()
    assert "uint8_t s3_parity;" in internal
    assert "int s3_sprite_parity" in internal
    assert "int s3_mesh_parity" in internal
    assert "int s3_mesh_mirror_y" in internal
    assert "RenderS3FlipGlyphY" in internal
    assert "RenderS3DepthOrientationParity(float depth_key)" in s3
    assert "arc > 0.5 * ASCK_CURVED_PI" in s3
    assert "s->s3_parity = (uint8_t)RenderS3DepthOrientationParity(z)" in gpu
    assert "RenderS3DepthOrientationParity(z)" in world
    assert "mesh_far_side = RenderS3Active(r) && r->s3_mesh_parity" in world
    assert "mesh_v[i][1]" in world
    assert "mesh_far_side ? pv_flip : pv" in world
    assert "GetInstBBox(inst, bbox)" in world
    assert "r->s3_mesh_mirror_y = (int)floorf(fy + 0.5f)" in world
    assert "buf->s3_parity = (uint8_t)RenderS3DepthOrientationParity(RenderS3LastDepth(r))" in sprites
    assert "fy = f->height - 1 - fy" in sprites
    assert "flipped_src.gl = RenderS3FlipGlyphY(flipped_src.gl)" in sprites
    assert "s3_parity_count >= 2" in resolve
    assert "ptr->gl = RenderS3FlipGlyphY(ptr->gl)" in resolve
    assert "r->s3_sprite_parity = buf->s3_parity ? 1 : 0" in scene


def test_resolve_water_fails_closed_under_curvature() -> None:
    """The resolve pass must not synthesize water when the server publishes a
    non-Euclidean curvature (water becomes world-model geometry)."""
    text = (REPO_ROOT / "engine" / "render" / "render_resolve.cpp").read_text()
    assert "ASCK_CURVATURE_KIND_EUCLIDEAN" in text, (
        "render_resolve.cpp lost its curvature fail-closed gate on the "
        "water synthesis branch"
    )
    assert "kSphericalSceneScale" not in _strip_comments(text), (
        "render_resolve.cpp still references the deleted render-owned "
        "spherical scene scale"
    )


def test_mp_step_hands_whole_step_to_s3_owner() -> None:
    """Runtime-ownership pin: under spherical curvature MpStepOnce returns
    from the S3 owner BEFORE the Euclidean soup collector is built — server
    pose changes happen through geodesic stepping, never chart dx/dy."""
    text = (REPO_ROOT / "server" / "mp_step.cpp").read_text()
    owns = text.find("MpStepS3Owns(env)")
    move = text.find("MpStepS3Move(env, input, dt, &next, &result)")
    chart = text.find("move_dx_world = dx * cosf(yaw_rad)")
    collect = text.find("collector.Build(env,")
    assert 0 < owns < move < chart < collect, (
        "the S3 ownership branch must run (and return) before the flat "
        "chart movement math and soup collector are reached"
    )
    header = (REPO_ROOT / "server" / "mp_step.h").read_text()
    for field in ("s3_ground", "s3_east", "s3_valid"):
        assert field in header, f"MpStepState must persist {field}"
    phys = (REPO_ROOT / "engine" / "physics_state.h").read_text()
    assert "s3_ground" in phys and "s3_valid" in phys
    physics_cpp = (REPO_ROOT / "engine" / "physics.cpp").read_text()
    assert physics_cpp.count("s3_ground") >= 3, (
        "Physics must store and Save/Restore the pose (it would otherwise "
        "be re-seeded from chart compat coordinates every tick)"
    )


def test_s3_pose_frame_crosses_snapshot_wire_to_render() -> None:
    """Spherical render/control must consume the transported S3 frame from the
    authoritative snapshot, not rebuild camera truth from chart pos + yaw."""
    protocol = (REPO_ROOT / "server" / "protocol" / "protocol_snapshot.h").read_text()
    assert "STRUCT_SNAPSHOT_ENTITY_S3_POSE" in protocol
    assert "SNAPSHOT_LAYOUT_VERSION_S3_POSE_FRAME" in protocol
    ingest = (REPO_ROOT / "engine" / "network_ingest_snapshot.cpp").read_text()
    assert "ent.s3_ground" in ingest and "ent.s3_east" in ingest
    bridge = (REPO_ROOT / "engine" / "game_render_bridge.cpp").read_text()
    assert "RendererSetS3AuthoritativePose" in bridge
    render = (REPO_ROOT / "engine" / "render" / "render_s3.cpp").read_text()
    assert "MpStepS3CameraPose" not in render
    assert "RenderS3BuildAuthoritativeCamera" in render


def test_s3_observability_report_fields_present() -> None:
    """Item 10 (in-repo halves): render report carries the S3 observability
    fields; the server JSON publishes geometry_mode and the player pose."""
    report = (REPO_ROOT / "engine" / "render" / "render_frame_report.h").read_text()
    for f in ("projection_identity", "primary_image_count", "antipodal_image_count",
              "sky_sample_count", "water_termination_count", "camera_pose4"):
        assert f in report, f"S3RenderReport missing {f}"
    tick = (REPO_ROOT / "server" / "server_tick.cpp").read_text()
    for f in ("geometry_mode", "player_pose4", "player_tangent_frame4"):
        assert f'\\"{f}\\"' in tick or f'"{f}"' in tick, f"authoritative JSON missing {f}"


def test_s3_terrain_subdivision_uses_embedded_midpoints() -> None:
    """Terrain image-0 conjugate-region subdivision must split on S3, not in
    chart-linear wx/wy/wz space. Chart-linear split is the FL-4257 starburst
    owner found by the 2026-06-12 ownership probe."""
    s3_header = (REPO_ROOT / "engine" / "render" / "render_s3.h").read_text()
    s3_impl = (REPO_ROOT / "engine" / "render" / "render_s3.cpp").read_text()
    render_pass = (REPO_ROOT / "engine" / "render" / "render_world_pass.cpp").read_text()
    assert "RenderS3TerrainMidpoint" in s3_header
    assert "RenderS3TerrainMidpoint" in s3_impl
    assert "RenderS3TerrainMidpoint(r, p1, p2, out)" in render_pass
    assert "0.5f * (p1.wx + p2.wx)" not in render_pass
    assert "kS3TerrainMaxDepth = 3" in render_pass
    assert "if (rec >= kS3TerrainMaxDepth)\n\t\t\treturn;" in render_pass
    assert "kS3TerrainHaloDropX" in render_pass


def test_s3_water_globe_has_bounded_leaf_policy() -> None:
    """Water stays real S3 geometry, but it must not rasterize oversized
    conjugate-region leaves at a deep recursion cap."""
    text = (REPO_ROOT / "engine" / "world_s3.cpp").read_text()
    assert "kS3WaterMaxDepth = 5" in text
    assert "kS3WaterLeafPx = 28.0f" in text
    assert "||\n\t\t\t    depth >= kS3WaterMaxDepth" not in text
    assert "ex <= kS3WaterLeafPx && ey <= kS3WaterLeafPx" in text


def test_s3_native_load_defers_terrain_dark_bootstrap() -> None:
    """Spherical native headed runs must reach gameplay before terrain-dark
    raycast bootstrap drains the full patch list."""
    text = (REPO_ROOT / "engine" / "mainmenu.cpp").read_text()
    assert "MainMenuRequestedGeometryIsSpherical()" in text
    assert "ASCIICKER_WORLD_GEOMETRY_SPHERICAL_V1" in text
    assert "Deferring terrain-dark bootstrap for spherical native run" in text


def test_s3_cpu_fallback_submits_dual_terrain_images() -> None:
    """The diagnostic CPU fallback still submits both S3 images; primary-only
    terrain creates blue water/sky gaps on the ground."""
    text = (REPO_ROOT / "engine" / "render" / "render_scene.cpp").read_text()
    assert "s3_cpu_terrain_images = s3_gpu_terrain ? 0 : 2" in text
    assert "final GPU lane must restore dual terrain image submission" not in text


def test_s3_movement_does_not_revive_coarse_mesh_blocker() -> None:
    """S3 mesh collision is still open work; a coarse QueryWorld bbox blocker
    freezes headed movement while jump and yaw still work."""
    text = (REPO_ROOT / "server" / "mp_step_s3.cpp").read_text()
    assert "S3MeshBlocked" not in text
    assert "S3BlockerMeshCB" not in text
    assert "QueryWorld(env.world" not in text


def test_s3_movement_uses_flat_speed_contract_once() -> None:
    """MpStepOnce owns input integration and normalized velocity. The S3
    geodesic owner must multiply by env.xy_speed once when turning that
    normalized velocity into arc length."""
    text = (REPO_ROOT / "server" / "mp_step_s3.cpp").read_text()
    assert "arc_speed = speed * (double)env.xy_speed" in text
    assert "ang = arc_speed * (double)dt / R" in text
    assert "ang = speed * (double)dt / R" not in text


def test_s3_movement_uses_movement_facing_not_camera_yaw() -> None:
    """S3 movement must not revive FL-3858 by making camera/body yaw own the
    sprite facing row. The spherical owner uses the same movement-facing front
    door as flat SP/MP."""
    text = (REPO_ROOT / "server" / "mp_step_s3.cpp").read_text()
    assert '#include "../engine/facing_space.h"' in text
    assert "FacingMovementStep(next->player_dir, move_e, move_n)" in text
    assert "next->player_dir = next->yaw" not in text


def test_s3_first_person_toggle_not_gated_by_mobile_controls() -> None:
    """ReadConf forces mobile controls on for mobile combat. First-person must
    not use that flag as a PC capability test."""
    text = (REPO_ROOT / "engine" / "game_input.cpp").read_text()
    match = re.search(
        r"void Game::ToggleS3FirstPerson\(\)\n\{(?P<body>.*?)\n\}",
        text,
        re.S,
    )
    assert match, "ToggleS3FirstPerson body not found"
    body = match.group("body")
    assert "session.mobile_controls" not in body
    assert "PC only" not in body
    assert "camera.s3_first_person = !camera.s3_first_person" in body

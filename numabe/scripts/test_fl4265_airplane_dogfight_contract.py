#!/usr/bin/env python3
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes

import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
COLYSEUS_ADAPTER = ROOT / "godot_project" / "scripts" / "net" / "ColyseusStateAdapter.gd"
WORLD = ROOT / "godot_project" / "scripts" / "World.gd"
PRODUCT = ROOT / "godot_project" / "render" / "product" / "cell_owner_product.glsl"
COMPOSITOR = ROOT / "godot_project" / "render" / "api" / "ASCIICompositorEffect.gd"
SERVER_ROOM = ROOT / "colyseus_server" / "src" / "rooms" / "AuthoritativeRoom.ts"
SERVER_WORLD = ROOT / "colyseus_server" / "src" / "sim" / "world.ts"
PROOF_RUNNER = ROOT / "scripts" / "run_fl4265_airplane_proof.py"
PROOF_HARNESS = ROOT / "godot_project" / "tools" / "proofs" / "run_fl4265_airplane_dogfight_proof.gd"


def test_colyseus_world_item_snapshot_forwards_structural_hp_for_damage_smoke() -> None:
    adapter = COLYSEUS_ADAPTER.read_text(encoding="utf-8")

    # Damage smoke follows the server-owned airplane structure, not the mounted
    # pilot. Pin the world-item mirror so a future actor-HP shortcut cannot
    # silently become a second authority for the aircraft's visible damage.
    world_item_body = adapter.split(
        "func _world_item_to_snapshot(item: Variant, pickup_id_string: String) -> Dictionary:",
        1,
    )[1].split("\nfunc ", 1)[0]
    assert '"hp": int(_read_field(item, "hp", 0))' in world_item_body
    assert '"max_hp": int(_read_field(item, "max_hp", 0))' in world_item_body


def test_airplane_damage_vfx_are_render_functions_of_server_snapshot() -> None:
    world = WORLD.read_text(encoding="utf-8")
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    vfx_body = world.split(
        "func _tick_airplane_pilot_damage_vfx(actors: Dictionary, npcs: Dictionary) -> void:",
        1,
    )[1].split("\nfunc ", 1)[0]
    vapor_style_body = product.split(
        "void vsb_product_apply_vapor_style(",
        1,
    )[1].split("\n}\n", 1)[0]

    required_world = [
        "const AIRPLANE_MAT_SMOKE := 211",
        "const AIRPLANE_MAT_EXPLOSION := 212",
        "const IMPACT_SHARD_COUNT := 13",
        "const AIRPLANE_SMOKE_DAMAGE_THRESHOLD := 0.45",
    ]
    for needle in required_world:
        assert needle in world, needle
    explosion_shards = re.search(
        r"^const EXPLOSION_SHARD_COUNT := (\d+)$", world, re.MULTILINE
    )
    assert explosion_shards is not None
    assert int(explosion_shards.group(1)) >= 19

    # The VFX function consumes server snapshot facts only. The killed-pilot
    # presentation caches retired from this path deliberately; requiring their
    # names made the contract red after the authority moved to airplane HP.
    required_vfx = [
        'var is_mounted := String(snapshot.get("mount_id", "")) == "airplane_vehicle"',
        'var is_dead := bool(snapshot.get("is_dead", false))',
        'var plane_pickup_id := _snapshot_mount_pickup_id(snapshot)',
        'var pilot_plane: Dictionary = _airplane_world_item_server_snapshots.get(plane_pickup_id, {})',
        'var is_airborne := is_mounted and not bool(pilot_plane.get("grounded", true))',
        'var plane_hp := int(pilot_plane.get("hp", 0))',
        'var plane_max_hp := int(pilot_plane.get("max_hp", 0))',
        "if plane_max_hp <= 0:",
        'var damage_ratio := clampf(1.0 - float(plane_hp) / float(plane_max_hp), 0.0, 1.0)',
        "if is_airborne and not is_dead and damage_ratio >= AIRPLANE_SMOKE_DAMAGE_THRESHOLD:",
        'if was_mounted and was_alive and was_airborne and is_dead:',
        "_push_airplane_smoke_dynamic_sample(",
        "_push_airplane_explosion_burst(",
        "_airplane_pilot_vfx_prev.erase(key)",
    ]
    for needle in required_vfx:
        assert needle in vfx_body, needle

    # A damage puff and an explosion shard are authored glyph particles, not
    # multi-cell cards which repeat one animated character over a rectangle.
    assert '"cell_particle": true' in world
    assert 'if kind == "explosion_sample":' in COMPOSITOR.read_text(encoding="utf-8")
    assert "FL4359_EFFECT_CHARACTER_WORLD_SIZE.x * 0.52" in COMPOSITOR.read_text(encoding="utf-8")
    destruction_vfx_body = world.split(
        "func _tick_destroyed_airplane_damage_vfx() -> void:", 1
    )[1].split("\nfunc ", 1)[0]
    assert "_push_airplane_explosion_burst(" in destruction_vfx_body
    assert "var generation := int(plane.get(\"destruction_generation\", 0))" in destruction_vfx_body
    assert "_airplane_dead_pilot_" not in vfx_body

    # Baseline A deliberately restyled material 211 from grey signs into the
    # wounded airplane's flickering blue damage flame. Pin that receipted law
    # inside the retained-vapor style function instead of reviving the older
    # grey-smoke expectation that the source stopped implementing.
    required_vapor_style = [
        "} else if (material == 211u) {",
        "uint phase_seed = vapor.facts.source_id * 2654435761u",
        "+ vapor.facts.legacy_face_index * 4u;",
        "params.feature_params.x * 11.0",
        "vec3(0.10, 0.38, 1.0),",
        "vec3(0.72, 0.94, 1.0), density) * twinkle;",
        "0.28 + 0.60 * density, 0.16, 0.90);",
    ]
    for needle in required_vapor_style:
        assert needle in vapor_style_body, needle


def test_presented_airplane_tail_is_the_only_mounted_wake_owner() -> None:
    world = WORLD.read_text(encoding="utf-8")
    compositor = COMPOSITOR.read_text(encoding="utf-8")

    assert "func _publish_wake_source_sample(" not in world
    assert "_publish_wake_source_sample(pickup_id, plane_source_id, pos)" not in world
    assert "not _mounted_on_airplane" in world
    assert "func set_player_world_xz_at_server_tick(" in compositor
    assert "emit_player_wake: bool = true" in compositor
    assert "if not emit_player_wake:\n\t\treturn true" in compositor
    assert "func _dynamic_mover_publish_airplane_trails(" in compositor
    assert "Vector3(-2.6, 0.55, 0.0) * model_scale" in compositor
    assert 'if grounded or bool(fact.get("waterborne", false)):' in compositor
    assert "push_wake_source_sample_at_server_time(" in compositor


def test_crash_capture_requires_real_npc_projectile_kill() -> None:
    room = SERVER_ROOM.read_text(encoding="utf-8")
    server_world = SERVER_WORLD.read_text(encoding="utf-8")
    runner = PROOF_RUNNER.read_text(encoding="utf-8")
    harness = PROOF_HARNESS.read_text(encoding="utf-8")

    # The retired proof hook directly assigned HP and called damageAirplane.
    # Keeping any part of it would leave a second crash owner beside combat.
    retired = [
        "FL4265_PROOF_CRASH_SEQUENCE",
        "FL4265_PROOF_CRASH_INITIAL_HP",
        "FL4265_PROOF_CRASH_DAMAGE_TICK",
        "FL4265_PROOF_CRASH_TARGET_PICKUP_ID",
        "applyProofCrashSequence",
        "proofCrashSequenceStaged",
    ]
    for needle in retired:
        assert needle not in room
        assert needle not in runner

    # Dogfight staging changes only spawn geometry. Damage still runs through
    # bot AI -> applyAirplaneFire -> projectile collision -> damageAirplane.
    required_room = [
        "const targetPlane = this.world.worldItems.get(AIRPLANE_PICKUP_ID);",
        "const botSpawn = dogfightBotSpawn(this.world, targetPlane);",
        "const botAirborneY = targetPlane?.grounded === false",
        "const botPlane = spawnAirplane(",
        "botPilot.position = { ...botPlane.position };",
    ]
    for needle in required_room:
        assert needle in room
    assert "airplaneBotMemory" not in server_world
    assert "extendTicks" not in server_world
    assert "const AIRPLANE_BOT_TAIL_DISTANCE = 18.0;" in server_world
    assert "export const AIRPLANE_MAX_HP = 300;" in server_world
    assert "const slotX = targetPosition.x - targetForwardX * AIRPLANE_BOT_TAIL_DISTANCE;" in server_world
    assert "const desiredThrottle = Math.max(0, Math.min(1," in server_world
    assert "lateralError <= AIRPLANE_BOT_FIRE_LATERAL_ERROR" in server_world
    assert "AIRPLANE_DAMAGE_EVENT" in server_world
    assert "owner_actor_id: projectile.ownerActorId" in server_world
    assert "target_pickup_id: planeHit.plane.pickupId" in server_world

    required_runner = [
        'CAPTURE_CHILD_NPC_CRASH_ENV = {',
        'GODOT_NATIVE_FULLSCREEN_ARG = "--fl4359-force-fullscreen"',
        "*([] if args.fl4265_crash_sequence_route else GODOT_FULLSCREEN_ARGS)",
        "cmd.append(GODOT_NATIVE_FULLSCREEN_ARG)",
        'cmd.append("--fl4265-crash-perspective")',
        'cmd.append("--fl4265-crash-performance")',
        "if args.fl4359_clean_play_no_capture or args.fl4265_crash_performance",
        '"AIRPLANE_SPAWN_THROTTLE": "0.85"',
        '"AIRPLANE_SPAWN_X": "86.0"',
        '"AIRPLANE_SPAWN_Z": "93.0"',
        "route_environment = CAPTURE_CHILD_NPC_CRASH_ENV",
        '"route": "server_npc_projectile_airplane_crash_sequence"',
        '"server_synthetic_damage_enabled": False',
        'if event.get("owner_actor_id") == BOT_PILOT_ACTOR_ID',
        'and event.get("target_pickup_id") == PRIMARY_AIRPLANE_PICKUP_ID',
        'and event.get("destroyed") is True',
        'receipt.get("camera_orbit_required") is True',
        'receipt.get("camera_orbit_observed") is True',
        'receipt.get("fullscreen_surface_display_backed") is True',
        '"fps_sample_eligible": args.fl4265_crash_performance',
        'int(receipt.get("fps_sample_count", 0)) > 0',
        "args.fl4359_clean_play_no_capture or args.fl4265_crash_performance",
        'receipt.get("frame_pacing", {}).get("uncapped") is True',
        '== GODOT_UNCAPPED_ARGS',
        'float(receipt.get("camera_orbit_delta_radians", 0.0)) >= 0.12',
        "and bool(npc_kill_events)",
    ]
    for needle in required_runner:
        assert needle in runner

    assert '"proof": "server_npc_projectile_airplane_crash_sequence"' in harness
    assert '"server_bot_tracer_snapshot_observed"' in harness
    assert '"server_synthetic_damage_enabled": false' in harness
    assert '"player_fire_input_used": false' in harness
    assert '"camera_orbit_required": true' in harness
    assert '"camera_orbit_observed": camera_orbit_observed' in harness
    assert '"fps_sample_eligible": fl4265_crash_performance' in harness
    assert '"clean_play_timing_uncontaminated": fl4265_crash_performance' in harness
    assert "performance_frame_times_ms.append" in harness
    # The two "no evidence side-work this frame" gates. FL-4265 2026-08-06 gave
    # them a shared name because a SECOND arm now needs the same behaviour: the
    # operator recording arm records a video and, like the measuring arm, must
    # not freeze to save a still. The gates themselves are what this contract
    # guards, so the needles follow the rename rather than the old spelling.
    #
    # Pinning the definition of the shared name is what keeps this test honest.
    # Without it, someone could satisfy the two gates below while quietly
    # widening the name to cover the PNG-saving arm as well, which would delete
    # the evidence stills this proof exists to produce. Requiring the definition
    # to be exactly "measuring arm OR recording arm" proves the plain capture
    # arm still takes the evidence path, and proves the measuring arm's own
    # behaviour is bit-for-bit what it was before the rename.
    assert (
        "var diagnostics_suppressed := fl4265_crash_performance"
        " or fl4265_crash_recording_route"
    ) in harness
    # Gate 1: only the arms that save nothing skip the physics-step wait, so the
    # PNG arm still advances one evidence row per physics step.
    assert "if not diagnostics_suppressed:\n\t\t\tawait physics_frame" in harness
    assert "/ 1000000.0 * 60.0" in harness
    # Gate 2: the arms that save nothing jump to the next frame BEFORE the
    # capture block, which is the whole reason those arms do not stall.
    assert "if diagnostics_suppressed:\n\t\t\tcontinue" in harness
    assert '"camera_orbit_progress_radians": camera_orbit_progress' in harness
    assert '"camera_projection": "perspective" if fl4265_crash_perspective else "orthographic"' in harness
    assert "Camera3D.PROJECTION_PERSPECTIVE" in harness
    assert "camera.fov = 55.0" in harness
    assert "96.0 if firing_phase else 82.0" in harness
    assert 'route_error = "healthy_plane_grounded_before_npc_kill"' in harness
    assert "_await_native_fullscreen_surface()" in harness
    assert '"fullscreen_surface_display_backed": fullscreen_surface_ready' in harness
    assert "func _set_fl4265_crash_fire_input(" not in harness
    assert "_await_canonical_fullscreen_surface()" in harness


if __name__ == "__main__":
    # Direct execution must collect this module through pytest and propagate
    # failures. Merely defining the tests previously returned zero without
    # exercising the dogfight damage and retained-vapor contracts.
    raise SystemExit(pytest.main([__file__, "-q"]))

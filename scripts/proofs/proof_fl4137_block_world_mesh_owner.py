#!/usr/bin/env python3
"""
FL-4137 placed-block ownership proof.

This is a source-shape guard only. It proves the old placed-block-only physics
owner and the later mesh-proxy owner are gone, and placed blocks feed mp_step
through the server-owned world entity registry. It is not runtime closure;
headed two-tab proof still owns gameplay evidence.
"""

from pathlib import Path
import re
import sys

REPO = Path(__file__).resolve().parents[2]


def read(rel):
    return (REPO / rel).read_text()


def strip_cpp_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//.*", "", text)
    return text


def fail(msg):
    print("FAIL:", msg)
    sys.exit(1)


def require(cond, msg):
    if not cond:
        fail(msg)


def require_absent(pattern, text, msg):
    if re.search(pattern, text, flags=re.S):
        fail(msg)


def main():
    mp_step_h = strip_cpp_comments(read("server/mp_step.h"))
    mp_step_cpp = strip_cpp_comments(read("server/mp_step.cpp"))
    mp_move_h = strip_cpp_comments(read("server/mp_move.h"))
    mp_move_cpp = strip_cpp_comments(read("server/mp_move.cpp"))
    server_tick = strip_cpp_comments(read("server/server_tick.cpp"))
    server_state = strip_cpp_comments(read("server/server_state.h"))
    world_registry = strip_cpp_comments(read("server/world_entity_registry.h"))
    item_state = strip_cpp_comments(read("server/authoritative_item_server_state.h"))
    appearance = strip_cpp_comments(read("engine/authoritative_world_item_appearance.cpp"))
    local_auth = strip_cpp_comments(read("engine/local_player_authority.cpp"))
    snapshot = strip_cpp_comments(read("engine/network_ingest_snapshot.cpp"))
    make_server = read("makefile_server")
    make_game = read("makefile_game_mac")
    build_web = read("build-web.sh")

    require(not (REPO / "engine/mp_diag_shadow_colliders.h").exists(),
            "old client shadow collider header still exists")
    require(not (REPO / "engine/mp_diag_shadow_colliders.cpp").exists(),
            "old client shadow collider TU still exists")
    for name, text in [
        ("makefile_server", make_server),
        ("makefile_game_mac", make_game),
        ("build-web.sh", build_web),
    ]:
        require("mp_diag_shadow_colliders" not in text,
                f"{name}: old shadow collider TU still in build")

    combined_runtime = "\n".join([
        mp_step_h,
        mp_step_cpp,
        mp_move_h,
        mp_move_cpp,
        server_tick,
        item_state,
        appearance,
        local_auth,
        snapshot,
    ])
    require_absent(r"struct\s+MpPlacedBlockCollider\s*\{", combined_runtime,
                   "MpPlacedBlockCollider struct still exists")
    require_absent(r"\bBuildShadowPlacedBlockColliders\s*\(", combined_runtime,
                   "client diagnostic shadow collider builder still exists/called")
    require_absent(r"\bMpStepResolvePlacedBlockSupport\s*\(", combined_runtime,
                   "W5 placed-block support resolver still exists/called")
    require_absent(r"\bPushPlacedBlock(?:Tri|Quad)\s*\(", combined_runtime,
                   "placed-block-only soup push still exists/called")
    require_absent(r"\bCollectPlacedBlocks\s*\(", combined_runtime,
                   "mp_step placed-block-only collector still exists/called")
    require_absent(r"\bplaced_collision_inst\b", combined_runtime,
                   "server placed collision Inst owner still exists")
    require_absent(r"\bcollision_inst\b", combined_runtime,
                   "client placed collision Inst mirror still exists")
    require_absent(r"\bSvrCreatePlacedBlockCollisionInst\b", combined_runtime,
                   "server collision mesh proxy creator still exists")
    require_absent(r"\bSyncAuthoritativePlacedBlockCollisionInst\b", combined_runtime,
                   "client collision mesh proxy mirror still exists")
    require_absent(r"\bMP_PLACED_BLOCK_STORY_ID_(?:BASE|MAX)\b", combined_runtime,
                   "placed-block story-id classifier still exists")
    require_absent(r"\blegacy_yy_block_collision\.akm\b", combined_runtime,
                   "legacy collision mesh proxy is still referenced by active source")
    require(not (REPO / "assets/meshes/legacy_yy_block_collision.akm").exists(),
            "legacy placed block collision AKM asset still exists")

    require(re.search(
        r"MpStepBuildEnv\s*\([^)]*Terrain\s*\*\s*terrain\s*,\s*World\s*\*\s*world\s*,\s*const\s+ServerWorldEntityRegistry\s*\*\s*world_entities\s*,\s*uint64_t\s+\w+\s*,\s*float\s+water_level\s*,\s*uint8_t\s+mount\s*\)",
        mp_step_h,
        flags=re.S),
        "MpStepBuildEnv does not take const ServerWorldEntityRegistry")

    require("ServerWorldEntityRegistry world_entities" in server_state,
            "ServerState does not own the world entity registry")
    require("uint64_t placed_entity_id" in server_state,
            "SvrItemState does not carry placed_entity_id")
    require("struct ServerWorldEntity" in world_registry,
            "ServerWorldEntity component record missing")
    require("SERVER_WORLD_ENTITY_MAX = 1024" in world_registry,
            "registry cap was not bumped above SVR_MAX_ITEMS")
    require("ServerWorldEntityRegistryInit" in world_registry,
            "registry explicit init API missing")
    require("ServerWorldEntityRegistryInit(&state->world_entities)" in server_tick,
            "server init/test reset does not explicitly initialize registry")
    require("ServerWorldEntityRegistryUpsertPlacedBlock" in world_registry,
            "registry upsert API missing")
    require("ServerWorldEntityRegistryRemoveByItemId" in world_registry,
            "registry remove API missing")
    require("SvrUpsertPlacedBlockEntity" in server_tick,
            "server placement path does not upsert registry entity")
    require("SvrRemovePlacedBlockEntity" in server_tick,
            "pickup/drop path does not remove registry entity")
    require("env.world_entities" in mp_step_cpp and "SERVER_WORLD_ENTITY_COLLIDABLE" in mp_step_cpp,
            "mp_step does not consume registry collidable entities")
    require("source_item_id" in mp_step_cpp and "MP_SUPPORT_PLACED_BLOCK" in mp_step_cpp,
            "mp_step support provenance does not derive placed block from entity item id")
    require_absent(r"\bsupport_top_z\b", world_registry,
                   "registry stores stale support_top_z instead of deriving top from pos+height")
    require("it->placed_flags = SVR_PLACED_ITEM_NONE;\n    it->placed_durability = 0;\n    it->placed_yaw = 0.0f;\n    SvrRemovePlacedBlockEntity" in server_tick,
            "pickup/drop does not clear placed flags before removing entity")

    print("PASS proof_fl4137_block_world_mesh_owner")
    print("  - old MpPlacedBlockCollider/CollectPlacedBlocks/W5/shadow-collider owners absent")
    print("  - mesh-proxy Inst/story-id owners absent")
    print("  - server placed items upsert/remove ServerWorldEntity records")
    print("  - mp_step consumes registry CollisionBody/SupportSurface boxes")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
FL-4128 / FL-4137 server collision component source-shape proof.

This is not gameplay closure. It only guards the architecture seam:
world meshes and placed-block world entities must be normalized inside
server/mp_step.cpp into one server-owned collision component, while the old
placed-block-only lane and mesh-proxy owner stay deleted.
"""

from pathlib import Path
import re
import sys


REPO = Path(__file__).resolve().parents[2]


def read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def require(cond: bool, msg: str) -> None:
    if not cond:
        print(f"FAIL: {msg}", file=sys.stderr)
        sys.exit(1)


def require_re(pattern: str, text: str, msg: str) -> None:
    require(re.search(pattern, text, re.S) is not None, msg)


def require_absent(pattern: str, text: str, msg: str) -> None:
    require(re.search(pattern, text, re.S) is None, msg)


def strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//.*", "", text)
    return text


def main() -> None:
    mp_step = read("server/mp_step.cpp")
    mp_step_h = read("server/mp_step.h")
    server_tick = read("server/server_tick.cpp")
    world_registry = read("server/world_entity_registry.h")
    runtime = "\n".join([mp_step, mp_step_h, server_tick])
    runtime_code = strip_comments(runtime)

    require_re(r"struct\s+MpCollisionBox\s*\{", mp_step,
               "MpCollisionBox proxy type missing")
    require_re(r"std::vector<MpCollisionBox>\s+boxes", mp_step,
               "collector does not own collision proxy boxes")
    require_re(r"QueryWorldCB\s+cb\s*=\s*\{\s*MeshCollect\s*,\s*SpriteCollect", mp_step,
               "world query callback is not wired to MeshCollect")
    require_re(r"QueryWorld\s*\(\s*env\.world\s*,[^;]+&cb", mp_step,
               "world meshes are not collected through QueryWorld callback")
    require_re(r"FL-4128/FL-4137: server-owned collision component", mp_step,
               "missing inline architecture marker at proxy creation")
    require_re(r"collector->boxes\.push_back", mp_step,
               "MeshCollect does not create collision proxy boxes")
    require_re(r"env\.world_entities", mp_step,
               "mp_step does not read server world entity registry")
    require_re(r"SERVER_WORLD_ENTITY_COLLIDABLE", mp_step,
               "world entity collidable component is not consumed")
    require_re(r"source_item_id", mp_step,
               "world entity item provenance is not carried into collision boxes")
    require_re(r"cached->boxes", mp_step,
               "mesh cache does not preserve collision proxy boxes")
    require_re(r"for\s*\(const\s+MpCollisionBox&\s+box\s*:\s*collector\.boxes\).*?box\.CheckCollision",
               mp_step,
               "sweep loop does not test collision proxy boxes")
    require_re(r"MpSupportHitForCollisionBox", mp_step,
               "support provenance for collision proxy boxes is missing")
    require_re(r"for\s*\(const\s+MpCollisionBox&\s+box\s*:\s*collector\.boxes\).*?support_top",
               mp_step,
               "support query does not read collision proxy top faces")
    require_re(r"static\s+constexpr\s+float\s+kMpCollisionBoxSupportMargin\s*=\s*1\.02f",
               mp_step,
               "collision-box support margin is missing")
    require_re(r"bool\s+ContainsSupportXY\s*\([^)]*\).*?kMpCollisionBoxSupportMargin",
               mp_step,
               "collision-box support XY does not use body-radius margin")
    require_re(r"if\s*\(!box\.ContainsSupportXY\s*\(\s*x\s*,\s*y\s*\)\)",
               mp_step,
               "support queries still check raw box top rectangle instead of body footprint")

    require_absent(r"struct\s+MpPlacedBlockCollider\s*\{", runtime_code,
                   "old MpPlacedBlockCollider owner reintroduced")
    require_absent(r"\bCollectPlacedBlocks\s*\(", runtime_code,
                   "old CollectPlacedBlocks owner reintroduced")
    require_absent(r"\bMpStepResolvePlacedBlockSupport\s*\(", runtime_code,
                   "old W5 placed-block support owner reintroduced")
    require_absent(r"MP_SOUP_SOURCE_MESH_PLACED_BLOCK\s*=", runtime_code,
                   "old PBLK magic mesh-id owner reintroduced")
    require_absent(r"\bSvrCreatePlacedBlockCollisionInst\b", runtime_code,
                   "old placed-block collision mesh proxy creator reintroduced")
    require_absent(r"\bMP_PLACED_BLOCK_STORY_ID_(?:BASE|MAX)\b", runtime_code,
                   "old placed-block story-id classifier reintroduced")
    require_re(r"struct\s+ServerWorldEntity\s*\{", world_registry,
               "ServerWorldEntity component record missing")
    require_re(r"ServerWorldEntityRegistryUpsertPlacedBlock", world_registry,
               "ServerWorldEntityRegistry upsert path missing")

    require_re(r"MpStepOnce\(step_state,\s*step_input,\s*step_env,\s*&step_result\)",
               server_tick,
               "server_tick no longer delegates player movement to MpStepOnce")

    print("PASS proof_fl4128_server_collision_component")
    print("  - server/mp_step owns collision proxy boxes")
    print("  - QueryWorld/MeshCollect and ServerWorldEntityRegistry feed collision boxes")
    print("  - sweep and support consume collision boxes")
    print("  - old placed-block-only owners remain deleted")


if __name__ == "__main__":
    main()

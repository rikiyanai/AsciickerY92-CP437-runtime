#!/usr/bin/env python3
"""FL-4131 JoinV2 content_pack_id identity wiring checks."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def main() -> int:
    errors: list[str] = []

    generated = read("engine/actor_visual_profile_table.generated.h")
    compiler = read("scripts/compile_actor_visual_profiles.py")
    native = read("engine/game_app.cpp")
    web = read("web/web_filesystem.cpp")
    server = read("server/appearance_contract_state.cpp")

    if 'ACTOR_VISUAL_PROFILE_CONTENT_PACK_ID = "material.additive.v1"' not in generated:
        errors.append("generated actor visual table does not advertise material.additive.v1 content_pack_id")
    if "def _compile_identity_content_pack_id" not in compiler:
        errors.append("actor visual compiler does not derive a compile-identity content_pack_id")
    if "strncpy(out->content_pack_id, ACTOR_VISUAL_PROFILE_CONTENT_PACK_ID" not in native:
        errors.append("native JoinV2 request does not send ACTOR_VISUAL_PROFILE_CONTENT_PACK_ID")
    if "g_web_content_pack_id" not in web or "ACTOR_VISUAL_PROFILE_CONTENT_PACK_ID" not in web:
        errors.append("web JoinV2 JSON does not send ACTOR_VISUAL_PROFILE_CONTENT_PACK_ID")
    if "snprintf(content_pack_id, sizeof(content_pack_id), \"%s\", ACTOR_VISUAL_PROFILE_CONTENT_PACK_ID)" not in server:
        errors.append("server startup contract does not bind ACTOR_VISUAL_PROFILE_CONTENT_PACK_ID")
    if "content_pack_id is not yet emitted" in (compiler + native + web + server):
        errors.append("stale empty-content-pack compatibility comment remains in runtime/compiler path")

    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    print("PASS: FL-4131 JoinV2 content_pack_id identity is generated and wired")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

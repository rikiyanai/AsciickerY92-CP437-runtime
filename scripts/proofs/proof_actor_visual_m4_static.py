#!/usr/bin/env python3
"""Static M4 proof for shield authoring and wolf mount-front mask data.

This is a pre-watchdog gate. It proves the generated table and server
reachability agree before a headed/browser run spends time exercising pixels.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE_FINAL = Path(
    "/Users/r/Desktop/bundle_layer_audit_20260520/"
    "verifier_state_backups/state_FINAL_20260521-163326.json"
)
UPSTREAM_REF = "upstream/master"
UPSTREAM_COMMIT = "8ff75d0c5a8d2745a8ad6a8a841dd31a46e81635"


def fail(message: str) -> None:
    raise SystemExit(f"proof_actor_visual_m4_static: {message}")


def run(args: list[str], *, text: bool = True) -> str:
    return subprocess.check_output(args, cwd=REPO_ROOT, text=text)  # type: ignore[return-value]


def read(path: str) -> str:
    return (REPO_ROOT / path).read_text()


def parse_generated_row_keys(generated: str) -> list[tuple[int, ...]]:
    row_keys: list[tuple[int, ...]] = []
    row_re = re.compile(
        r'"normal_player\.[^"]+",\s*\{\s*([0-9,\s{}]+?)\s*\},\s*\d+,',
        re.S,
    )
    for match in row_re.finditer(generated):
        nums = [int(x) for x in re.findall(r"\d+", match.group(1))]
        if len(nums) < 14:
            fail("compiled row key has fewer than 14 scalar fields")
        row_keys.append(tuple(nums[:14]))
    return row_keys


def reachable_key_tuple(key: dict[str, int]) -> tuple[int, ...]:
    return (
        key["skin_id"],
        key["actor_style_id"],
        key["presentation_kind_id"],
        key["variation_id"],
        key["mount_id"],
        key["rig_id"],
        key["head_item_id"],
        key["head_style_id"],
        key["chest_item_id"],
        key["chest_style_id"],
        key["weapon_item_id"],
        key["weapon_style_id"],
        key["shield_item_id"],
        key["shield_style_id"],
    )


def compile_reachability_dump() -> Path:
    out = Path("/private/tmp/actor_visual_reachability_dump_m4_static")
    subprocess.check_call(
        [
            "c++",
            "-std=c++17",
            "-I.",
            "server/actor_visual_reachability_dump.cpp",
            "-o",
            str(out),
        ],
        cwd=REPO_ROOT,
    )
    return out


def assert_state_final(state_path: Path) -> None:
    state = json.loads(state_path.read_text())
    expected = {
        "player-0010-L2": ("accept", "player_shield_regular"),
        "bigbee-0010-L3": ("accept", "bigbee_shield_regular"),
        "plydie-0010-L3": ("accept", "shield"),
        "wolfie-0010-L4": ("partial", "shield bit only for wolfie"),
        "wolfie-0002-L4": ("accept", "wolfie_weapon_crossbow"),
        "wolfie-1010-L4": ("accept", "wolfie_armor_regular"),
    }
    for key, (want_status, want_label_fragment) in expected.items():
        row = state.get(key)
        if not row:
            fail(f"state_FINAL missing {key}")
        if row.get("status") != want_status:
            fail(f"state_FINAL {key} status={row.get('status')!r}, want {want_status!r}")
        label = str(row.get("corrected_label") or row.get("pre_guess") or row.get("note") or "")
        if want_label_fragment.lower() not in label.lower():
            fail(f"state_FINAL {key} label={label!r}, want fragment {want_label_fragment!r}")


def assert_upstream_assets() -> None:
    commit = run(["git", "rev-parse", "--verify", UPSTREAM_REF]).strip()
    if commit != UPSTREAM_COMMIT:
        fail(f"{UPSTREAM_REF}={commit}, want {UPSTREAM_COMMIT}")
    for name in [
        "attack-0011.xp",
        "bigbee-0010.xp",
        "player-0010.xp",
        "plydie-0010.xp",
        "wolfie-0002.xp",
        "wolfie-0010.xp",
        "wolfie-1010.xp",
    ]:
        upstream = subprocess.check_output(
            ["git", "cat-file", "-p", f"{UPSTREAM_REF}:sprites/{name}"],
            cwd=REPO_ROOT,
        )
        local = (REPO_ROOT / "assets" / "sprites" / name).read_bytes()
        if hashlib.sha256(upstream).hexdigest() != hashlib.sha256(local).hexdigest():
            fail(f"local assets/sprites/{name} does not match {UPSTREAM_REF}:sprites/{name}")


def assert_generated_table() -> None:
    generated = read("engine/actor_visual_profile_table.generated.h")
    catalog = read("server/actor_visual_catalog_source.h")
    if "APPEARANCE_SLOT_KIND_SHIELD, 0,    0,   APPEARANCE_CATALOG_GAMEPLAY_WEARABLE" not in catalog:
        fail("shield item 402 is not catalog-wearable")
    if (
        '"wolfie_body_front__L2__mount_front", '
        "ACTOR_VISUAL_SEMANTIC_MASK_METHOD_AUTHORED_CELL_SET"
    ) not in generated:
        fail("wolfie mount-front semantic mask is not authored")
    if (
        '"wolfie_body_front__L2__mount_front", '
        "ACTOR_VISUAL_SEMANTIC_MASK_METHOD_ALL_VISIBLE"
    ) in generated:
        fail("wolfie mount-front semantic mask still uses all-visible")
    if "kActorVisualSemanticMaskCells_70" not in generated:
        fail("wolfie mount-front authored cell set is missing")

    row_keys = parse_generated_row_keys(generated)
    if len(row_keys) != 192:
        fail(f"compiled row count={len(row_keys)}, want 192")
    if sum(1 for key in row_keys if key[12] == 402) != 96:
        fail("compiled table does not contain 96 shield-key rows")
    if generated.count("ACTOR_VISUAL_LAYER_ROLE_SHIELD") < 96:
        fail("compiled table does not emit shield role layers")

    dump_bin = compile_reachability_dump()
    dump = json.loads(subprocess.check_output([str(dump_bin)], text=True))
    reachable = [reachable_key_tuple(row["key"]) for row in dump["reachable_keys"]]
    if dump["reachable_key_count"] != 192:
        fail(f"reachable_key_count={dump['reachable_key_count']}, want 192")
    if sum(1 for key in reachable if key[12] == 402) != 96:
        fail("server reachability does not contain 96 shield keys")
    row_key_set = set(row_keys)
    missing = [key for key in reachable if key not in row_key_set]
    orphan = [key for key in row_key_set if key not in set(reachable)]
    if missing:
        fail(f"missing reachable compiled rows: {len(missing)}")
    if orphan:
        fail(f"orphan compiled rows outside reachability: {len(orphan)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-final", type=Path, default=DEFAULT_STATE_FINAL)
    args = parser.parse_args(argv)
    assert_state_final(args.state_final)
    assert_upstream_assets()
    assert_generated_table()
    print("proof_actor_visual_m4_static: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

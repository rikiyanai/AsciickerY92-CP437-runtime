import hashlib
import struct
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "addons" / "io_asciicker" / "scene"))

from a3d_format import (  # noqa: E402
    A3DHeader,
    A3DInstance,
    A3DMaterial,
    A3DMinimapMarker,
    A3DPatch,
    A3DPlayerStart,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_approved_character_and_rocket_assets_are_exact() -> None:
    sprites = REPO_ROOT / "assets" / "sprites"
    wallace = sprites / "2026-08-12-030327-wallace.xp"
    gromit = sprites / "2026-08-12-030327-gromit.xp"
    assert _sha256(wallace) == "0e2bd7823d3aab79007df8a1c6c58150b5bb3c7718a75ce0ae9df27e88adbc3a"
    assert _sha256(gromit) == "e2e2a4212fb57c70ffc615c4c8539e336f7a9bc08b88e6c3648f37ec2a9b5bb5"
    assert (sprites / "player-0000.xp").read_bytes() == wallace.read_bytes()
    assert (sprites / "wolfie-0000.xp").read_bytes() == gromit.read_bytes()
    assert _sha256(REPO_ROOT / "assets/meshes/source/toy_rocket.glb") == (
        "6990ca861b55d4afd5073aa1cc09b018eb6617324a618c52215ca40ded271263"
    )
    assert _sha256(REPO_ROOT / "assets/meshes/toy_rocket_ship.akm") == (
        "5225c421a0321f75aa058b449af876c708915e87b09fe2968743a1bed7e343ed"
    )


def test_canonical_map_is_flat_sand_with_start_and_nearby_rocket() -> None:
    path = REPO_ROOT / "assets" / "a3d" / "game_map_y8.a3d"
    with path.open("rb") as stream:
        header = A3DHeader.from_file(stream)
        patches = [A3DPatch.from_file(stream) for _ in range(header.num_patches)]
        materials = [A3DMaterial.read(stream) for _ in range(256)]
        version = -struct.unpack("<i", stream.read(4))[0]
        instance_count = struct.unpack("<i", stream.read(4))[0]
        instances = [A3DInstance.from_file(stream, version) for _ in range(instance_count)]
        has_start = struct.unpack("<i", stream.read(4))[0]
        player_start = A3DPlayerStart.from_file(stream) if has_start else None
        enemy_count = struct.unpack("<i", stream.read(4))[0]
        marker_count = struct.unpack("<i", stream.read(4))[0]
        markers = [A3DMinimapMarker.from_file(stream) for _ in range(marker_count)]
        assert stream.read() == b""

    assert version == 4
    assert patches
    assert {cell for patch in patches for row in patch.visual for cell in row} == {4}
    assert {height for patch in patches for row in patch.height for height in row} == {128}
    sand_cells = [cell for ramp in materials[4].shade for cell in ramp]
    assert all(cell.fg[0] >= cell.fg[1] > cell.fg[2] for cell in sand_cells)
    assert all(cell.bg[0] >= cell.bg[1] > cell.bg[2] for cell in sand_cells)
    assert enemy_count == 0
    assert player_start is not None
    assert len(instances) == 1
    rocket = instances[0]
    assert rocket.mesh_name == "toy_rocket_ship.akm"
    assert rocket.flags == 3
    dx = rocket.transform[12] - player_start.pos[0]
    dy = rocket.transform[13] - player_start.pos[1]
    assert dx * dx + dy * dy < 12.0 * 12.0
    assert [(marker.name, marker.label) for marker in markers] == [("rocket", "Rocket")]


def test_gromit_relationship_is_server_owned_and_damage_closed() -> None:
    state = (REPO_ROOT / "server/server_state.h").read_text(encoding="utf-8")
    tick = (REPO_ROOT / "server/server_tick.cpp").read_text(encoding="utf-8")
    assert "SVR_NPC_COMPANION" in state
    assert "owner_player_id" in state
    assert "SvrEnsurePlayerCompanion(state, ci)" in tick
    assert '"gromit_companion"' in tick
    assert "npc->appearance.mount_definition_id = 950" in tick
    assert tick.count("npc->disposition == SVR_NPC_COMPANION") >= 4
    assert "WorldGetPlayerStart(state->world, map_pos)" in tick
    assert tick.count("SvrSeedLegacyBlockWorldItem(state, &cache)") == 0

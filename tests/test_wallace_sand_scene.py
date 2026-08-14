import hashlib
import json
import math
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


def _akm_vertex_colors(path: Path) -> set[tuple[int, int, int, int]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    vertex_count = int(
        next(line for line in lines if line.startswith("element vertex ")).split()[-1]
    )
    first_vertex = lines.index("end_header") + 1
    return {
        tuple(map(int, line.split()[-4:]))
        for line in lines[first_vertex:first_vertex + vertex_count]
    }


def test_approved_character_and_rocket_assets_are_exact() -> None:
    sprites = REPO_ROOT / "assets" / "sprites"
    wallace = sprites / "2026-08-12-030327-wallace.xp"
    gromit = sprites / "2026-08-12-030327-gromit.xp"
    assert _sha256(wallace) == "0e2bd7823d3aab79007df8a1c6c58150b5bb3c7718a75ce0ae9df27e88adbc3a"
    assert _sha256(gromit) == "e2e2a4212fb57c70ffc615c4c8539e336f7a9bc08b88e6c3648f37ec2a9b5bb5"
    assert _sha256(sprites / "player-0000.xp") == (
        "5cbb0d4ac1b9d0e0dc721ed5cd30bec8eafd6afcab98383a77b8116d0859acad"
    )
    assert _sha256(sprites / "wolfie-0000.xp") == (
        "0a71765714abf3539828432330ab02a6875ae4f017be72316b086dc99e150eb8"
    )
    assert _sha256(REPO_ROOT / "assets/meshes/source/toy_rocket.glb") == (
        "6990ca861b55d4afd5073aa1cc09b018eb6617324a618c52215ca40ded271263"
    )
    assert _sha256(REPO_ROOT / "assets/meshes/toy_rocket_ship.akm") == (
        "ceb99d7aa06a00a9555f7bef8f6158e1c8857059215571a270ea376e2d714d7f"
    )
    assert _akm_vertex_colors(REPO_ROOT / "assets/meshes/toy_rocket_ship.akm") == {
        (255, 102, 0, 0),
        (204, 204, 204, 0),
    }
    converter = (REPO_ROOT / "scripts/picocad_to_akm.py").read_text(encoding="utf-8")
    assert 'material_index = prim.get("material", 0)' in converter
    assert "resolve_texture_path(gltf, gltf_dir, material_index)" in converter
    receipt = json.loads(
        (REPO_ROOT / "docs/recordings/wallace-gromit-upright-rocket.receipt.json")
        .read_text(encoding="utf-8")
    )
    assert receipt["artifact"]["sha256"] == _sha256(
        REPO_ROOT / receipt["artifact"]["path"]
    )
    assert receipt["rocket"]["akm_sha256"] == _sha256(
        REPO_ROOT / "assets/meshes/toy_rocket_ship.akm"
    )


def test_canonical_map_is_rolling_sand_with_start_and_large_nearby_rocket() -> None:
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
    vertex_heights: dict[tuple[int, int], set[int]] = {}
    for patch in patches:
        for row, heights in enumerate(patch.height):
            for col, height in enumerate(heights):
                key = (patch.x * 4 + col, patch.y * 4 + row)
                vertex_heights.setdefault(key, set()).add(height)
    # A3D stores shared patch edges twice. One world-coordinate height owner
    # must produce identical values on both copies or the terrain will crack.
    assert all(len(values) == 1 for values in vertex_heights.values())
    terrain = {key: next(iter(values)) for key, values in vertex_heights.items()}
    unique_heights = set(terrain.values())
    assert len(unique_heights) >= 160
    assert min(unique_heights) >= 80
    assert max(unique_heights) <= 320
    assert max(unique_heights) - min(unique_heights) >= 180

    local_peaks = 0
    max_adjacent_step = 0
    for (x, y), height in terrain.items():
        neighbors = [
            terrain.get((x - 1, y)),
            terrain.get((x + 1, y)),
            terrain.get((x, y - 1)),
            terrain.get((x, y + 1)),
        ]
        present = [neighbor for neighbor in neighbors if neighbor is not None]
        if len(present) == 4 and all(height > neighbor for neighbor in present):
            local_peaks += 1
        if present:
            max_adjacent_step = max(
                max_adjacent_step,
                max(abs(height - neighbor) for neighbor in present),
            )
    assert local_peaks >= 50
    assert max_adjacent_step <= 32
    spawn_x = round(player_start.pos[0] / 2.0) if player_start else -2
    spawn_y = round(player_start.pos[1] / 2.0) if player_start else -37
    nearby_heights = [
        height
        for (x, y), height in terrain.items()
        if abs(x - spawn_x) <= 20 and abs(y - spawn_y) <= 20
    ]
    assert max(nearby_heights) - min(nearby_heights) >= 120
    sand_cells = [cell for ramp in materials[4].shade for cell in ramp]
    assert all(cell.fg[0] >= cell.fg[1] > cell.fg[2] for cell in sand_cells)
    assert all(cell.bg[0] >= cell.bg[1] > cell.bg[2] for cell in sand_cells)
    assert enemy_count == 0
    assert player_start is not None
    assert len(instances) == 1
    rocket = instances[0]
    assert rocket.mesh_name == "toy_rocket_ship.akm"
    assert rocket.flags == 3
    assert rocket.transform[0] == 4.0
    assert rocket.transform[6] == -64.0
    assert rocket.transform[9] == 4.0
    assert all(rocket.transform[index] == 0.0 for index in (1, 2, 4, 5, 8, 10))
    dx = rocket.transform[12] - player_start.pos[0]
    dy = rocket.transform[13] - player_start.pos[1]
    assert 25.0 < math.hypot(dx, dy) < 32.0
    start_key = (round(player_start.pos[0] / 2.0), round(player_start.pos[1] / 2.0))
    rocket_key = (round(rocket.transform[12] / 2.0), round(rocket.transform[13] / 2.0))
    assert player_start.pos[2] == terrain[start_key]
    assert math.isclose(
        rocket.transform[14],
        terrain[rocket_key] + 64.0 * 4.409,
        abs_tol=1e-4,
    )
    assert [(marker.name, marker.label) for marker in markers] == [("rocket", "Rocket")]


def test_gromit_relationship_is_server_owned_and_damage_closed() -> None:
    state = (REPO_ROOT / "server/server_state.h").read_text(encoding="utf-8")
    tick = (REPO_ROOT / "server/server_tick.cpp").read_text(encoding="utf-8")
    assert "SVR_NPC_COMPANION" in state
    assert "owner_player_id" in state
    assert "follow_active" in state
    assert "SvrEnsurePlayerCompanion(state, ci)" in tick
    assert '"gromit_companion"' in tick
    assert "APPEARANCE_CATALOG_GROMIT_PROFILE_ID" in tick
    assert "npc->mount_state = MOUNT::NONE" in tick
    assert "npc->appearance.mount_definition_id = 950" not in tick
    assert "if (npc->follow_active && dist > 0.01f)" in tick
    assert tick.count("npc->disposition == SVR_NPC_COMPANION") >= 4
    assert "WorldGetPlayerStart(state->world, map_pos)" in tick
    assert tick.count("SvrSeedLegacyBlockWorldItem(state, &cache)") == 0

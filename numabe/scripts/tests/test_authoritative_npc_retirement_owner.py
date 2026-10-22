from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GAME_CPP = ROOT / "engine" / "game.cpp"
RENDER_BRIDGE_CPP = ROOT / "engine" / "game_render_bridge.cpp"


def _authoritative_init_block() -> str:
    source = GAME_CPP.read_text(encoding="utf-8")
    start = source.index("\tif (authoritative_multiplayer)")
    end = source.index("\n\tstrcpy(g->player.name", start)
    return source[start:end]


def test_authoritative_init_is_the_single_local_npc_retirement_owner() -> None:
    block = _authoritative_init_block()

    assert block.count("RetireLocalNpcActorsForAuthoritativeMode(g);") == 1
    assert block.index("RetireLocalNpcActorsForAuthoritativeMode(g);") < block.index(
        "PurgeWorldItemInsts(world);"
    )
    assert "local_npcs_retired = true" not in block


def test_render_bridge_cannot_retire_or_delete_local_npc_instances() -> None:
    source = RENDER_BRIDGE_CPP.read_text(encoding="utf-8")

    assert "RetireLocalNpcActorsForAuthoritativeMode" not in source
    assert "DeleteInst(h_vis->inst)" not in source
    assert "local NPC retire/delete" not in source

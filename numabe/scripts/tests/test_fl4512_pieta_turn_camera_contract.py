"""FL-4512 camera-turn contract for the slow live Pieta renderer."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BRIDGE = (ROOT / "engine/game_render_bridge.cpp").read_text(encoding="utf-8")
AUTHORITY = (ROOT / "engine/local_player_authority.cpp").read_text(encoding="utf-8")
HEADER = (ROOT / "engine/local_player_authority.h").read_text(encoding="utf-8")


def test_render_stall_identity_reaches_the_single_yaw_integration_owner() -> None:
    assert "const bool render_stall = _stamp - stamp > 500000;" in BRIDGE
    assert "_stamp, stamp, render_stall, physics, &io" in BRIDGE
    assert "uint64_t stamp,\n    bool render_stall," in HEADER
    assert "uint64_t stamp, bool render_stall," in AUTHORITY
    assert "render_stall, bool is_server_session" in AUTHORITY


def test_slow_frame_turn_uses_the_existing_quarter_second_catchup_bound() -> None:
    assert "if (dt_sec > 0.25f) dt_sec = 0.25f;" in AUTHORITY
    assert (
        "if (render_stall && dt_sec == 0.0f && fabsf(io->torque) > 0.001f)"
        in AUTHORITY
    )
    assert "dt_sec = 0.25f;" in AUTHORITY
    assert "io->yaw += io->torque * mp_yaw_rate_deg_per_sec * dt_sec;" in AUTHORITY

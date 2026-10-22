"""Static contract for the FL-4512 Step-0 live selector operand dump."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PRESENTER = ROOT / "platform/terminal_gl_present.cpp"


def _bridge_body() -> str:
    source = PRESENTER.read_text(encoding="utf-8")
    bridge = source.split("int TermDumpBridgeCells", 1)[1]
    return bridge.split("static void TermFl4548EmitCellTrace", 1)[0]


def test_bridge_emits_the_exact_live_scene_fact_fields_once() -> None:
    """The trace must expose the selector operand, not a diagnostic plane."""
    bridge = _bridge_body()

    assert bridge.count('\\"scene_fact_valid\\":%u,') == 1
    assert bridge.count('\\"harri_shape6_valid\\":%u,') == 1
    assert bridge.count(
        '\\"harri_shape6_q0_16\\":[%u,%u,%u,%u,%u,%u],'
    ) == 1
    assert 'scene_fact_shape6_valid' not in bridge
    assert 'scene_fact_shape6_q0_16' not in bridge


def test_bridge_binds_each_live_field_once_before_diagnostic_planes() -> None:
    """One SceneFactV1 row supplies the live fields before GPU readback data."""
    bridge = _bridge_body()
    diagnostic = bridge.index("b.internal6[idx * 6 + 0]")

    assert bridge.count("b.fl4683_scene_fact[idx].valid") == 1
    assert bridge.count("b.fl4683_scene_fact[idx].harri_shape6_valid") == 1
    for axis in range(6):
        assert bridge.count(
            f"b.fl4683_scene_fact[idx].harri_shape6_q0_16[{axis}]"
        ) == 1

    live_last = bridge.rindex("b.fl4683_scene_fact[idx].harri_shape6_q0_16[5]")
    assert live_last < diagnostic

"""Delete-first guards for the retired pre-HTML FL-4548 proof harness."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_pre_html_proof_executables_are_physically_absent():
    for relative in (
        "scripts/fl4548/fl4548_compile_proof_plan.py",
        "scripts/fl4548/fl4548_verify_graph_closure.py",
    ):
        assert not (ROOT / relative).exists(), relative


def test_generated_leaves_require_runtime_intent_physical_input():
    generator = (
        ROOT / "scripts/fl4260_generate_live_leaves.py"
    ).read_text(encoding="utf-8")
    assert "fl4548_compile_proof_plan.py" not in generator
    assert "fl4548.proof_plan.v4" not in generator
    assert "registered RuntimeIntentOwner control" in generator
    assert "live TERM++ frame" in generator


def test_dual_frame_grouped_harness_is_physically_absent():
    for relative in (
        "scripts/fl4588/live_grouped_capture_coordinator.py",
        "scripts/fl4548/fl4548_assemble_grouped_proof_package.py",
    ):
        assert not (ROOT / relative).exists(), relative

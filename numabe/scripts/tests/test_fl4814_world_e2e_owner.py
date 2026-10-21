import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts/sbu_e2e_run.py"


def test_numabe_requirements_bind_run_local_step3_to_canonical_mapping():
    requirements_path = (
        ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915"
        "/e2e_requirements.json"
    )
    requirements = json.loads(requirements_path.read_text(encoding="utf-8"))
    compiler_inputs = requirements["compiler_inputs"]
    assert compiler_inputs["observation_module_mapping_root"].endswith(
        "numabe_full_727_mapping_20260915"
    )
    assert compiler_inputs["phase1_receipt_names"]["step3"] == (
        "observed_feature_join_receipt.json"
    )


def test_asciiid_backend_is_explicit_at_historical_callers():
    runner = RUNNER.read_text(encoding="utf-8")
    assert 'default="direct"' in runner
    assert 'default="classic_cp437"' in runner
    assert 'default="world_ir"' in runner
    assert "SBU OSM → asciiid single-command E2E pipeline" not in runner
    for relative in (
        "scripts/launcher.py",
        "scripts/launcher_lib/option_tree.py",
        "scripts/sbu_sac_verify_run.py",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        for occurrence in text.split('"scripts/sbu_e2e_run.py"')[1:]:
            assert "sbu_legacy" in occurrence[:240], relative
            assert "asciiid_legacy" in occurrence[:240], relative

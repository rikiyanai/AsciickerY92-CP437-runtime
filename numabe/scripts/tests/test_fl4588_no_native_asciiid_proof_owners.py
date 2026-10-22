"""FL-4588/FL-4162: native default is allowed, stale proof owners are not."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
GATE_STATUS = SCRIPTS / "fl4131_gate_status.py"
HISTORICAL_PLAN = ROOT / "docs/plans/2026-06-01-fl4131-glyph-shape-lab-plan.md"


def test_retired_native_shape_lab_drivers_and_verifier_are_absent() -> None:
    assert not (SCRIPTS / "proofs/proof_fl4131_shape_lab_asciiid.js").exists()
    assert not (SCRIPTS / "proofs/proof_fl4131_editor_terrain_atlas.js").exists()
    assert not (SCRIPTS / "test_fl4131_asciiid_mouse_click_visible_grid.py").exists()
    assert not (SCRIPTS / "test_fl4131_asciiid_preset_ux_layout.py").exists()
    assert not (SCRIPTS / "test_fl4131_asciiid_preset_tooltips.py").exists()
    assert not (SCRIPTS / "fl4260_native_parity_gate.py").exists()
    assert not (SCRIPTS / "fl4260_termpp_pipeline_alignment_gate.py").exists()
    assert not (SCRIPTS / "fl4208/run_r2_heldout_selector_parity.py").exists()
    assert not (SCRIPTS / "fl4208/test_r2_parser_gates.py").exists()
    assert not (ROOT / "scratchpad/fl4208_n1_1bii_raw_parity.js").exists()
    assert not (
        ROOT
        / "docs/agent/glyph-rendering-paper/scripts/fl4512_capture_pieta_edge_yaw.py"
    ).exists()
    assert not (
        ROOT
        / "docs/agent/glyph-rendering-paper/scripts/fl4512_pieta_edge_yaw_common.py"
    ).exists()
    assert not (SCRIPTS / "tests/test_fl4512_pieta_yaw_capture_provenance.py").exists()
    assert not (SCRIPTS / "tests/test_fl4512_pieta_yaw_verifier.py").exists()
    assert not (
        ROOT
        / "docs/agent/glyph-rendering-paper/scripts/fl4512_render_yaw_insets_panel.py"
    ).exists()
    assert not (
        ROOT
        / "docs/agent/glyph-rendering-paper/scripts/fl4512_verify_yaw_insets.py"
    ).exists()
    assert not (
        ROOT
        / "docs/agent/glyph-rendering-paper/figures/panel-yaw-runtime-insets.png"
    ).exists()


def test_paper_capture_contract_rejects_native_stdout_and_window_pixels() -> None:
    paper = (
        ROOT / "docs/agent/glyph-rendering-paper/2026-07-06-fl4512-paper-draft.md"
    ).read_text(encoding="utf-8")
    assert "native editor pixels" in paper
    assert "fl4260_native_parity_gate.py" not in paper


def test_no_live_script_accepts_headed_native_asciiid_cdp_as_proof() -> None:
    offenders: list[str] = []
    for path in SCRIPTS.rglob("*"):
        if not path.is_file() or "adhoc" in path.parts:
            continue
        if path == Path(__file__):
            continue
        if path.suffix not in {".py", ".js", ".sh"}:
            continue
        if "headed_asciiid_cdp" in path.read_text(
            encoding="utf-8", errors="replace"
        ):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_fl4131_gates_keep_native_receipts_evidence_only() -> None:
    source = GATE_STATUS.read_text(encoding="utf-8")
    assert "proof_fl4131_shape_lab_asciiid.js" not in source
    assert "headed_asciiid_cdp" not in source
    assert "STATUS_PROOF_PASSED" not in source.split(
        "def check_asciiid_extended_glyph_browser_visible", 1
    )[1].split("def check_unknown_glyph_fallback_all_targets", 1)[0]
    assert (
        "historical native ImGui/CDP receipts are inadmissible" in source
    )
    assert "No HTML-only Shape Lab grouped receipt exists" in source


def test_old_shape_lab_plan_is_visibly_tombstoned() -> None:
    text = HISTORICAL_PLAN.read_text(encoding="utf-8")
    first_lines = "\n".join(text.splitlines()[:14])
    assert "HISTORICAL — DO NOT EXECUTE THE NATIVE ASCIIID STEPS" in first_lines
    assert "Historical receipts remain evidence-only" in first_lines


def test_native_default_keeps_normalized_xp_compiler_deletion_gates() -> None:
    compiler = (ROOT / "scripts/compile_actor_visual_profiles.py").read_text(
        encoding="utf-8"
    )
    runtime = (ROOT / "engine/actor_visual_profile_runtime.h").read_text(
        encoding="utf-8"
    )
    profile = (ROOT / "engine/actor_visual_profile.h").read_text(encoding="utf-8")

    assert "UPSTREAM_CONTRACT_FREEZE" in compiler
    assert "UPSTREAM_CONTRACT_DECISIONS" in compiler
    assert "PROFILE_BINDINGS" in compiler
    assert "SERVER_REACHABILITY" in compiler
    assert "_require_deleted_legacy_owners" in compiler
    assert "family_contract_freeze_sha256" in compiler
    assert "cell_role_decisions_sha256" in compiler
    assert '"runtime_authoritative": True' in compiler
    assert "assets/actor_visual_profiles/source/layer_roles.json" in compiler
    assert "scripts/promote_entries_to_layer_roles.py" in compiler

    assert not re.search(r"^def _resolve_source_xp\\b", compiler, re.MULTILINE)
    assert not re.search(
        r"^def _enumerate_server_reachable_keys\\b", compiler, re.MULTILINE
    )
    assert '"merge_extra_layers": True' not in compiler
    assert "ACTOR_VISUAL_LAYER_ROLE_" not in runtime
    assert "ACTOR_VISUAL_LAYER_ROLE_" not in profile


def test_native_default_keeps_generated_hash_admission_contract() -> None:
    generated = (ROOT / "engine/actor_visual_profile_table.generated.h").read_text(
        encoding="utf-8"
    )
    coverage = (ROOT / "scripts/check_actor_visual_table_coverage.py").read_text(
        encoding="utf-8"
    )

    assert "ACTOR_VISUAL_PROFILE_SOURCE_BINDINGS_SHA256" in generated
    assert "ACTOR_VISUAL_PROFILE_SERVER_REACHABILITY_ARTIFACT_SHA256" in generated
    assert "ACTOR_VISUAL_PROFILE_UPSTREAM_CONTRACT_FREEZE_SHA256" in generated
    assert "ACTOR_VISUAL_PROFILE_UPSTREAM_CELL_DECISIONS_SHA256" in generated

    assert "ACTOR_VISUAL_PROFILE_SOURCE_BINDINGS_SHA256" in coverage
    assert "ACTOR_VISUAL_PROFILE_UPSTREAM_CONTRACT_FREEZE_SHA256" in coverage
    assert "DEFAULT_DEPLOY_IDENTITY" in coverage
    assert "deploy_identity_sha256" in coverage
    assert "compiler_cutover_complete" in coverage
    assert "runtime_authoritative" in coverage
    assert "per_key_legacy_merge_fallback_deleted" in coverage

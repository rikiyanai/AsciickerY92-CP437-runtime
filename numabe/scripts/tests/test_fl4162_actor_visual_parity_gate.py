import importlib.util
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

SPEC = importlib.util.spec_from_file_location(
    "analyze_runs_fl4162", SCRIPTS_DIR / "analyze_runs.py"
)
assert SPEC and SPEC.loader
ANALYZE_RUNS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ANALYZE_RUNS)


LIVE_ACTOR_VISUAL_SURFACES = (
    REPO_ROOT / "engine/debug_telemetry_state.h",
    REPO_ROOT / "engine/game_debug_telemetry.cpp",
    REPO_ROOT / "engine/game_render_bridge.cpp",
    REPO_ROOT / "engine/remote_mounted_witness.cpp",
    REPO_ROOT / "web/web_recorder_bridge.cpp",
    REPO_ROOT / "scripts/proofs/proof_candidate_two_tab_smoothing.js",
    REPO_ROOT / "scripts/analyze_runs.py",
)


def _sample() -> dict:
    return {
        "tab_id": "tab1",
        "rel_s": 4.5,
        "actor_profile_selector_found": 1,
        "actor_appearance_skin_definition_id": 101,
        "actor_server_presentation_kind_id": 600,
        "actor_appearance_variation_id": 0,
        "actor_appearance_mount_definition_id": 0,
        "actor_appearance_rig_id": 0,
        "actor_appearance_slot_kind_ids": [300, 306],
        "actor_appearance_definition_ids": [101, 411],
        "actor_appearance_visual_style_ids": [0, 0],
        "actor_render_key_skin_id": 101,
        "actor_render_key_actor_style_id": 0,
        "actor_render_key_presentation_kind_id": 600,
        "actor_render_key_variation_id": 0,
        "actor_render_key_mount_id": 0,
        "actor_render_key_rig_id": 0,
        "actor_render_key_head_item_id": 0,
        "actor_render_key_head_style_id": 0,
        "actor_render_key_chest_item_id": 411,
        "actor_render_key_chest_style_id": 0,
        "actor_render_key_weapon_item_id": 0,
        "actor_render_key_weapon_style_id": 0,
        "actor_render_key_shield_item_id": 0,
        "actor_render_key_shield_style_id": 0,
        "actor_render_key_future_slot_kind_ids": [0, 0, 0, 0],
        "actor_render_key_future_item_ids": [0, 0, 0, 0],
        "actor_render_key_future_style_ids": [0, 0, 0, 0],
        "actor_profile_layer_count": 1,
        "actor_render_layer_semantic_contributions": [
            ["player_body", "player_armor_regular"]
        ],
        "actor_render_layer_semantic_contribution_set_indices": [12],
        "actor_render_layer_source_layer_indices": [2],
        "actor_render_layer_contributed_cell_counts": [10],
        "actor_render_fail_visible_fallback_used": 0,
        "actor_render_original_compose_failure_stage": 0,
    }


def test_valid_composite_sample_passes_every_contract_gate() -> None:
    result = ANALYZE_RUNS.derive_fl4162_actor_visual_parity_for_samples([_sample()])

    assert result["reason"] == "ok"
    assert all(result["gates"].values())
    assert result["composite_witnesses"] == [
        {
            "index": 0,
            "tab": "tab1",
            "rel_s": 4.5,
            "layer_index": 0,
            "semantic_contributions": ["player_body", "player_armor_regular"],
            "semantic_contribution_set_index": 12,
            "source_layer_index": 2,
            "contributed_cell_count": 10,
        }
    ]
    assert result["visual_operator_signoff_required"] is True


def test_retired_render_plan_fields_cannot_activate_the_profile_gate() -> None:
    sample = _sample()
    sample.pop("actor_profile_selector_found")
    sample["actor_server_visual_key_valid"] = 1
    sample["actor_render_plan_found"] = 1
    sample["actor_render_plan_layer_count"] = 1

    result = ANALYZE_RUNS.derive_fl4162_actor_visual_parity_for_samples([sample])

    assert result["reason"] == "no_active_compiled_actor_visual_sample"
    assert result["active_sample_count"] == 0
    assert not any(result["gates"].values())


def test_live_actor_visual_surfaces_do_not_publish_retired_plan_fields() -> None:
    for path in LIVE_ACTOR_VISUAL_SURFACES:
        source = path.read_text(encoding="utf-8")
        assert "actor_render_plan" not in source, path
        assert "actor_server_visual_key" not in source, path
        assert "actor_bundle_" not in source, path


def test_server_and_renderer_key_mismatch_fails_closed() -> None:
    sample = _sample()
    sample["actor_render_key_chest_item_id"] = 999

    result = ANALYZE_RUNS.derive_fl4162_actor_visual_parity_for_samples([sample])

    assert result["reason"] == "actor_render_key_chest_item_id_mismatch"
    assert not result["gates"]["gameplay_fl4162_exact_server_key_selected"]
    assert not result["gates"]["gameplay_fl4162_runtime_contract_parity"]


def test_missing_semantic_names_fails_evidence_and_composite_gates() -> None:
    sample = _sample()
    sample.pop("actor_render_layer_semantic_contributions")

    result = ANALYZE_RUNS.derive_fl4162_actor_visual_parity_for_samples([sample])

    assert result["reason"] == "semantic_contribution_shape_mismatch"
    assert not result["gates"]["evidence_fl4162_runtime_contract_fields_present"]
    assert not result["gates"]["gameplay_fl4162_composite_semantics_rendered"]
    assert result["gates"]["gameplay_fl4162_exact_server_key_selected"]


def test_empty_semantic_contribution_set_fails_closed() -> None:
    sample = _sample()
    sample["actor_render_layer_semantic_contributions"] = [[]]

    result = ANALYZE_RUNS.derive_fl4162_actor_visual_parity_for_samples([sample])

    assert result["reason"] == "semantic_contribution_shape_mismatch"
    assert not result["gates"]["evidence_fl4162_runtime_contract_fields_present"]


def test_later_valid_sample_does_not_erase_prior_contradiction() -> None:
    bad = _sample()
    bad["actor_render_fail_visible_fallback_used"] = 1

    result = ANALYZE_RUNS.derive_fl4162_actor_visual_parity_for_samples(
        [bad, _sample()]
    )

    assert result["valid_sample_count"] == 1
    assert result["contradictions"][0]["reason"] == "legacy_fail_visible_fallback_used"
    assert not result["gates"]["gameplay_fl4162_runtime_contract_parity"]


def test_missing_legacy_sentinel_is_missing_evidence() -> None:
    sample = _sample()
    sample.pop("actor_render_fail_visible_fallback_used")

    result = ANALYZE_RUNS.derive_fl4162_actor_visual_parity_for_samples([sample])

    assert result["reason"] == "missing_actor_render_fail_visible_fallback_used"
    assert not result["gates"]["evidence_fl4162_runtime_contract_fields_present"]
    assert not result["gates"]["gameplay_fl4162_legacy_fallback_absent"]


def test_missing_recording_fails_every_gate(tmp_path: Path) -> None:
    result = ANALYZE_RUNS.derive_fl4162_actor_visual_parity_for_artifact(tmp_path)

    assert result["reason"] == "recording_jsonl_missing"
    assert not any(result["gates"].values())


def test_artifact_recording_is_analyzed(tmp_path: Path) -> None:
    recorder = tmp_path / "recorder"
    recorder.mkdir()
    (recorder / "recording.jsonl").write_text(
        json.dumps({"kind": "sample", **_sample()}) + "\n", encoding="utf-8"
    )

    result = ANALYZE_RUNS.derive_fl4162_actor_visual_parity_for_artifact(tmp_path)

    assert result["gates"]["gameplay_fl4162_runtime_contract_parity"] is True
    assert result["recording_path"].endswith("recorder/recording.jsonl")

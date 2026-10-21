"""Analyzer ownership tests after production admission integration."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import fl4512_mesh_feature_candidate_admission_v2 as A  # noqa: E402

TOOL_PATH = ROOT / "scripts/adhoc/2026-08-17-FL-4512-U2-faithful-runtime-domain-grammar-counterfactual.py"
SPEC = importlib.util.spec_from_file_location("fl4512_u2_counterfactual", TOOL_PATH)
TOOL = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(TOOL)


def test_analyzer_imports_production_owners_directly():
    assert TOOL.build_combined_grammar is A.build_grammar
    assert TOOL._full_supply_constrained_exchange is A._full_supply_constrained_exchange
    assert TOOL._full_supply_signature_universe is A._full_supply_signature_universe
    assert TOOL._u7_cost_matrix_block is A._u7_cost_matrix_block


def test_analyzer_has_no_duplicate_semantic_definitions():
    source = TOOL_PATH.read_text(encoding="utf-8")
    assert "def build_combined_grammar" not in source
    assert "def _full_supply_constrained_exchange" not in source
    assert "def _full_supply_signature_universe" not in source


def test_production_grammar_is_the_frozen_analyzer_grammar():
    grammar = TOOL.build_combined_grammar()
    assert grammar["grammar_id"] == A.GRAMMAR_ID
    assert grammar["grammar_hash"] == (
        "f501bd27acae6b6209f7906eedc7dc0aac7f5478948c2235c22143e0ea5b50d7"
    )
    assert grammar["target_count"] == 1111
    assert grammar["legacy_330_count"] == 330


def test_preflight_reports_read_only_artifact_identity(tmp_path):
    artifact = tmp_path / "artifact.json"
    artifact.write_text(
        '{"schema":"%s","artifact_content_hash":"a","grammar_hash":"b",'
        '"receipt":{"content_hash":"c","distinct_signature_count":7}}'
        % TOOL.COMPILER.ARTIFACT_SCHEMA,
        encoding="utf-8",
    )
    args = type("Args", (), {"admission_artifact": artifact})()
    result = TOOL.grammar_preflight(args)
    assert result["selection_input"]["catalog_loaded"] is False
    assert result["selection_input"]["heldout_loaded"] is False
    assert result["grammar"]["target_count"] == 1111


def test_full_supply_operation_estimate_claims_operations_not_time():
    estimate = TOOL._full_supply_operation_estimate(99094, 1111)
    assert estimate["exact_u7_total_operations"] > 0
    assert estimate["time_estimate_not_claimed_before_execution"] is True


def test_full_supply_row_freezes_before_heldout_loader(monkeypatch, tmp_path):
    events = []
    descriptors = [{"glyph_id": gid, "mask8": gid,
                    "endpoint_count": 0, "junction_count": 0}
                   for gid in range(1, 33)]
    artifact = {
        "source": {"catalog_source_sha256": "c", "registry_source_sha256": "r"},
        "artifact_content_hash": "artifact", "grammar_hash": "grammar",
    }
    identity = {"catalog": {"sha256": "c"}, "registry": {"sha256": "r"}}
    monkeypatch.setattr(TOOL, "_load_artifact", lambda *unused: artifact)
    monkeypatch.setattr(TOOL, "_validated_supply",
                        lambda *unused: (descriptors, identity))
    monkeypatch.setattr(TOOL, "build_combined_grammar",
                        lambda: {"target_count": 1, "_targets_runtime": [
                            {"_mask8": 1, "endpoint_count": 0,
                             "junction_count": 0, "family_membership": []}
                        ]})
    monkeypatch.setattr(TOOL, "_validate_frozen_full_supply_contract",
                        lambda *unused: None)
    monkeypatch.setattr(TOOL, "_full_supply_signature_universe",
                        lambda supply: (supply, {"candidate_count": len(supply)}))
    monkeypatch.setattr(TOOL, "_artifact_selected_ids", lambda *unused: list(range(1, 33)))
    def exchange_stub(*unused, **kwargs):
        events.append("selection")
        return {"final_selected_descriptors": descriptors,
                "final_selected_glyph_ids": list(range(1, 33)),
                "iterations": 0, "legacy_metrics": {"worst": 0}}
    monkeypatch.setattr(TOOL, "_full_supply_constrained_exchange", exchange_stub)
    def heldout_loader(*unused):
        events.append("heldout")
        return {"per_unique_merged_mask": [], "per_unique_silhouette_only_mask": []}
    monkeypatch.setattr(TOOL, "_validate_heldout", heldout_loader)
    monkeypatch.setattr(TOOL, "_heldout_result_capacity32",
                        lambda *unused: {"deltas": {}})
    monkeypatch.setattr(TOOL, "_score_targets", lambda *unused: [0])
    monkeypatch.setattr(TOOL, "_capacity32_decision", lambda *unused: {"capacity32_go": False})
    monkeypatch.setattr(TOOL, "_sha256_file", lambda *unused: "a" * 64)
    args = type("Args", (), {
        "admission_artifact": tmp_path / "artifact.json",
        "catalog": tmp_path / "catalog.json",
        "registry": tmp_path / "registry.json",
        "heldout": tmp_path / "heldout.json",
    })()
    result = TOOL.run_full_supply_constrained(args, {"git_head": "h"}, events)
    assert events == ["selection", "row_frozen", "heldout"]
    assert result["selection"]["row_frozen_before_heldout"] is True


def test_cli_rejects_conflicting_modes():
    assert TOOL.main(["--grammar-preflight", "--full-supply-constrained"]) == 2

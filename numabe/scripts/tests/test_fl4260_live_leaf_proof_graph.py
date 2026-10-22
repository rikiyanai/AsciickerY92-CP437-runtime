#!/usr/bin/env python3
"""Regression coverage for the sole live-leaf proof-plan contract."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / "scripts/fl4260_generate_live_leaves.py"
INVENTORY_GRAPH = (
    ROOT / "docs/research/ascii/verification/fl4260/INVENTORY_GRAPH.json"
)


def _load_generator_module():
    spec = importlib.util.spec_from_file_location("fl4260_generate_live_leaves", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fact_text(facts: dict[str, str]) -> str:
    return "\n".join(facts.values())


def _assert_anchor_contains(facts: dict[str, str], symbol: str) -> None:
    source_path, line_text = facts["source anchor"].rsplit(":", 1)
    source_lines = (ROOT / source_path).read_text(encoding="utf-8").splitlines()
    assert symbol in source_lines[int(line_text) - 1]


def test_generator_dry_run_has_no_retired_plan_branch() -> None:
    completed = subprocess.run(
        [sys.executable, str(GENERATOR), "--dry-run"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "[fl4260-leaf-gen] DRY-RUN" in completed.stdout


def test_live_leaf_proof_graph_has_one_v4_owner() -> None:
    # The v4 plan owner retired in 8e7c9e6b4 (legacy authoring surfaces);
    # unpinned leaves carry the explicit harness-lane fallback instead.
    source = GENERATOR.read_text(encoding="utf-8")
    proof_graph = source.split('lines.append("## Proof Graph")', 1)[1]
    proof_graph = proof_graph.split('return "\\n".join(lines)', 1)[0]

    assert "graph_closed_plan_leaf" not in proof_graph
    assert 'lines.append("  -> fl4548/proof_plan.v4.schema.json")' not in proof_graph
    assert 'lines.append("  -> fl4548/proof_plan.schema.json")' not in proof_graph
    assert "harness lane FL-4548 / RQ-201c once pinned" in proof_graph


def test_dry_run_graph_uses_generated_leaf_text_in_memory() -> None:
    source = GENERATOR.read_text(encoding="utf-8")
    assert "leaf_text_by_path[leaf_rel] = text" in source
    assert "leaf_text = leaf_text_by_path.get(leaf_path)" in source


def test_mesh_asset_leaf_has_authored_html_source_facts() -> None:
    source = GENERATOR.read_text(encoding="utf-8")
    assert 'SOURCE_FACT_OVERRIDES["fl4588_html_mesh_asset_combo"]' in source
    assert '"source anchor": "web/asciiid-authoring-shell/src/ActiveToolOptions.tsx:118"' in source
    assert '"source line": "onChange={(event) => void onPick(event.target.value)}"' in source
    assert '"expected TERM++ delta class": "empty_final_termpp_editor_session_selection"' in source


def test_document_open_commit_has_visible_picker_source_facts() -> None:
    source = GENERATOR.read_text(encoding="utf-8")
    assert 'SOURCE_FACT_OVERRIDES["target_workspace_document_open_commit"]' in source
    assert '"source anchor": "web/asciiid-authoring-shell/src/WorkspaceActions.tsx:138"' in source
    assert '"expected TERM++ delta class": "full_document_replacement_final_cell_delta"' in source


def test_document_save_has_visible_download_source_facts() -> None:
    source = GENERATOR.read_text(encoding="utf-8")
    assert 'SOURCE_FACT_OVERRIDES["asciiid_interaction_workspace_file_save"]' in source
    assert '"source anchor": "web/asciiid-authoring-shell/src/WorkspaceActions.tsx:171"' in source
    assert '"expected TERM++ delta class": "empty_final_termpp_document_download"' in source


def test_material_history_row_463_uses_typed_batch_persistence() -> None:
    module = _load_generator_module()
    facts = module.SOURCE_FACT_OVERRIDES[
        "fl4260_row_463__persist_save_material_look"
    ]
    text = _fact_text(facts)

    assert "Fl4512ServiceQueuedProfileAutosave" not in text
    assert "frame autosave" not in text.lower()
    assert "Fl4260CommitProfileBatchWithHistory" in text
    assert "prepares URDO_Fl4260ProfilePrepare before installing candidate_states" in text
    assert "Fl4260CommitProfileBatch" in text
    assert facts["pipeline gap reason"] == "headed_html_transaction_receipt_absent"
    _assert_anchor_contains(facts, "Fl4260CommitProfileBatchWithHistory")


def test_material_history_row_464_retires_native_reload() -> None:
    module = _load_generator_module()
    facts = module.SOURCE_FACT_OVERRIDES[
        "fl4260_row_464__persist_revert_from_disk"
    ]
    text = _fact_text(facts)

    assert "native reload" not in text.lower()
    assert 'ImGui::Button("Revert' not in text
    assert "CommitAsciiidHistoryUndo" in text
    assert "CommitAsciiidHistoryRedo" in text
    assert "Fl4260ProfileUndoSwapPublishBatch" in text
    assert facts["pipeline gap reason"] == "headed_html_history_receipt_absent"
    _assert_anchor_contains(facts, "Fl4260ProfileUndoSwapPublishBatch")


def test_material_history_row_655_merges_into_workspace_undo() -> None:
    module = _load_generator_module()
    facts = module.SOURCE_FACT_OVERRIDES[
        "fl4260_row_655__material_look_one_step_undo"
    ]
    text = _fact_text(facts)

    assert "Merged observation of canonical workspace.history.undo" in text
    assert "Fl4260CommitProfileBatchWithHistory" in text
    assert "URDO_Fl4260ProfilePrepare" in text
    assert "Fl4260ProfileUndoSwapPublishBatch" in text
    assert "Fl4260CommitProfileBatch" in text
    assert "Fl4260SnapshotProfileEdit" not in text
    assert "Fl4260UndoProfileEdit" not in text
    _assert_anchor_contains(facts, "CommitAsciiidHistoryUndo")


def test_material_profile_history_symbols_resolve_in_shared_source() -> None:
    asciiid_source = (ROOT / "apps/asciicker-presenter/asciiid.cpp").read_text(encoding="utf-8")
    urdo_source = (ROOT / "apps/asciicker-presenter/urdo.cpp").read_text(encoding="utf-8")

    assert "CommitAsciiidMaterialScoreParameter" in asciiid_source
    assert "Fl4260CommitProfileBatchWithHistory" in asciiid_source
    assert "Fl4260CommitProfileBatch" in asciiid_source
    assert "Fl4260ProfileUndoSwapPublishBatch" in asciiid_source
    assert "CommitAsciiidHistoryUndo" in asciiid_source
    assert "CommitAsciiidHistoryRedo" in asciiid_source
    assert "URDO_Fl4260ProfilePrepare" in urdo_source
    assert "URDO_Fl4260ProfileLink" in urdo_source
    assert "URDO_Fl4260ProfileDiscard" in urdo_source


def test_material_profile_history_prepare_publish_order_and_old_owners_absent() -> None:
    asciiid_source = (ROOT / "apps/asciicker-presenter/asciiid.cpp").read_text(encoding="utf-8")
    helper = asciiid_source.split(
        "static bool Fl4260CommitProfileBatchWithHistory(", 1
    )[1].split("// FL-4260 RQ-147 LOAD.", 1)[0]
    swap = asciiid_source.split(
        "bool Fl4260ProfileUndoSwapPublishBatch(", 1
    )[1].split(
        "static bool Fl4260CommitProfileBatchWithHistory(", 1
    )[0]

    prepare_at = helper.index("URDO_Fl4260ProfilePrepare(")
    candidate_at = helper.index("= candidate_states[i]")
    durable_at = helper.index("Fl4260CommitProfileBatch(keys, count")
    assert prepare_at < candidate_at < durable_at
    assert "Fl4260CommitProfileBatch(keys, count" in swap

    for retired_definition in (
        "static void Fl4512ServiceQueuedProfileAutosave(",
        "static bool Fl4260SnapshotProfileEdit(",
        "static bool Fl4260UndoProfileEdit(",
        "static void Fl4260ApplyProfileDirectEdit(",
        "Fl4512AutosaveStateV1",
        "g_fl4260_autosave_pending_profile_key",
        'ImGui::Button("Undo last change##rq147undo")',
        "FL4512_DUMP_AUTOSAVE_STATE",
    ):
        assert retired_definition not in asciiid_source


def test_material_history_inventory_rows_remain_proof_open() -> None:
    module = _load_generator_module()
    for control_uid in (
        "fl4260_row_463__persist_save_material_look",
        "fl4260_row_464__persist_revert_from_disk",
        "fl4260_row_655__material_look_one_step_undo",
    ):
        assert control_uid not in module.PINNED_RECEIPTS


def test_repeatable_control_uid_dry_run_writes_nothing() -> None:
    selected_paths = (
        INVENTORY_GRAPH,
        ROOT / "docs/research/ascii/verification/fl4260/scene.objects.mesh.combo",
        ROOT / (
            "docs/research/ascii/verification/fl4260/EDIT_WITH_MATERIAL_LOOK/"
            "Material_Look_side_container/RENDERING_Glyph_Pools/"
            "0463__persist.save_material_look.md"
        ),
    )
    before = {path: path.read_bytes() for path in selected_paths}
    completed = subprocess.run(
        [
            sys.executable,
            str(GENERATOR),
            "--dry-run",
            "--control-uid",
            "fl4260_row_463__persist_save_material_look",
            "--control-uid",
            "fl4588_html_mesh_asset_combo",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "DRY-RUN: 2 leaves" in completed.stdout
    assert {path: path.read_bytes() for path in selected_paths} == before


def test_control_uid_selector_fails_shut_for_unknown_and_hand_authored() -> None:
    for control_uid, reason in (
        ("not-a-live-control", "unknown --control-uid target"),
        (
            "fl4260_row_429__material_selectable",
            "--control-uid targets hand-authored leaf",
        ),
    ):
        completed = subprocess.run(
            [sys.executable, str(GENERATOR), "--dry-run", "--control-uid", control_uid],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode != 0
        assert reason in completed.stderr


def test_inventory_graph_state_is_canonical_recorded_provenance() -> None:
    graph = json.loads(INVENTORY_GRAPH.read_text(encoding="utf-8"))
    summary = graph["summary"]
    assert "previously_generated_unchanged" not in summary

    canonical_states = {
        "HAND_AUTHORED",
        "GENERATED_FROM_CSV_AND_DATED",
        "GENERATED_FROM_CSV_ONLY",
    }
    assert {row["leaf_state"] for row in graph["rows"]} <= canonical_states
    assert summary["hand_authored"] == sum(
        row["leaf_state"] == "HAND_AUTHORED" for row in graph["rows"]
    )
    assert summary["generated_from_csv_and_dated"] == sum(
        row["leaf_state"] == "GENERATED_FROM_CSV_AND_DATED"
        for row in graph["rows"]
    )
    assert summary["generated_from_csv_only"] == sum(
        row["leaf_state"] == "GENERATED_FROM_CSV_ONLY" for row in graph["rows"]
    )
    assert summary["rows_with_missing_fields"] == 0

    rows_by_uid = {row["control_uid"]: row for row in graph["rows"]}
    for control_uid in (
        "fl4260_row_463__persist_save_material_look",
        "fl4260_row_464__persist_revert_from_disk",
        "fl4260_row_655__material_look_one_step_undo",
        "fl4588_html_mesh_asset_combo",
        "target_workspace_document_open_commit",
        "asciiid_interaction_workspace_file_save",
    ):
        assert rows_by_uid[control_uid]["missing_fields"] == []
        assert rows_by_uid[control_uid]["proof_status"] == "PROOF_OPEN"
    assert rows_by_uid["fl4588_html_mesh_asset_combo"][
        "source_anchor_status"
    ] == "AUTHORED_FILE_LINE"
    assert rows_by_uid["target_workspace_document_open_commit"][
        "source_anchor_status"
    ] == "AUTHORED_FILE_LINE"

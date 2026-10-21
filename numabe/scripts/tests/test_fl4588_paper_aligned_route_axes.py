import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PAPER = ROOT / "docs/agent/glyph-rendering-paper/2026-07-07-fl4512-first-rough-draft.tex"
REGISTRY = ROOT / "assets/asciiid/ui/asciiid.ui_registry.v1.json"
INVENTORY = ROOT / "docs/research/ascii/verification/fl4260/CURRENT_UI_INVENTORY.csv"
TARGET_LEAF = (
    ROOT
    / "docs/research/ascii/verification/fl4260/TARGET_ASCIIID_INVENTORY"
    / "0669__vertical_relation_density_grid.md"
)
CONTROL_UID = "fl4260_target_669__vertical_relation_density_grid"


def test_paper_keeps_structural_and_tonal_route_coordinates_separate() -> None:
    paper = PAPER.read_text(encoding="utf-8")
    assert "Tonal addresses can use ramp and density." in paper
    assert (
        "Terrain structural\naddresses can use vertical relation, rendered material "
        "surface direction, and flow."
    ) in paper
    assert (
        "when an edge, direction, or flow lane claims the cell, $P(c)$ is that "
        "lane's single bucket"
    ) in paper
    assert "edge_density" not in paper


def test_generated_joint_axis_is_a_tombstone_not_backend_debt() -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    leaf = next(
        item for item in registry["leaves"]
        if item["control_uid"] == CONTROL_UID
    )
    assert leaf["status"] == "tombstoned"
    assert leaf["migration_status"] == "tombstone"
    assert leaf["old_owner_deleted"] is True
    assert leaf["action_schema"] is None
    assert leaf["application_service_handler"] is None
    assert leaf["proof_status"] == "TOMBSTONE_DECISION"
    assert "edge_density" not in (leaf["source_owner"] or "")
    assert leaf["expected_termpp_scope"] == "No independent TERM++ scope."


def test_inventory_and_leaf_record_the_paper_conflict() -> None:
    with INVENTORY.open(newline="", encoding="utf-8") as handle:
        row = next(
            item for item in csv.DictReader(handle)
            if item["control_uid"] == CONTROL_UID
        )
    assert row["pipeline_status"] == "PIPELINE_TOMBSTONED"
    assert row["expectation_status"] == "tombstoned_target_contract_conflict"
    assert row["termpp_verdict"] == "NO_INDEPENDENT_DELTA"

    target = TARGET_LEAF.read_text(encoding="utf-8")
    assert "TOMBSTONED TARGET-CONTRACT CONFLICT" in target
    assert "No `edge_density[7][4]` ABI is added." in target

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL_PATH = ROOT / "scripts" / "pipeline" / "retag_a3d_shore_materials.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("retag_a3d_shore_materials", TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tool = _load_tool()


def _write_fixture(path: Path, patches: list[object], tail: bytes = b"TAIL") -> bytes:
    header = tool.fmt.A3DHeader(len(patches))
    with path.open("wb") as handle:
        header.write(handle)
        for patch in patches:
            patch.write(handle)
        handle.write(tail)
    return path.read_bytes()


def _flat_patch(x: int, y: int, *, material: int, height: int):
    patch = tool.fmt.A3DPatch(x, y)
    patch.visual = [
        [material for _ in range(tool.fmt.VISUAL_CELLS)]
        for _ in range(tool.fmt.VISUAL_CELLS)
    ]
    patch.height = [
        [height for _ in range(tool.fmt.HEIGHT_CELLS + 1)]
        for _ in range(tool.fmt.HEIGHT_CELLS + 1)
    ]
    patch.diag = 0xA55A
    return patch


def test_retags_only_selected_low_material_bytes_across_patch_boundary(
    tmp_path: Path,
) -> None:
    land = _flat_patch(0, 0, material=4, height=64)
    water = _flat_patch(1, 0, material=0, height=32)
    # Global (7, 3): mat2, above water, east neighbor is canonical water and
    # west neighbor supplies the sole eligible grass material. Preserve 0xCD.
    land.visual[3][6] = 0xAB01
    land.visual[3][7] = 0xCD02
    fixture = tmp_path / "shore.a3d"
    before = _write_fixture(fixture, [land, water], tail=b"material-table-and-tail")

    output, report = tool.build_result(fixture)

    assert report["selected_cell_count"] == 1
    assert report["replacement_counts"] == {"1": 1}
    offset = tool.fmt.A3DHeader.SIZE + tool.PATCH_VISUAL_OFFSET + (3 * 8 + 7) * 2
    assert output[offset] == 1
    assert output[offset + 1] == 0xCD
    assert output[:offset] == before[:offset]
    assert output[offset + 1 :] == before[offset + 1 :]
    assert report["changed_byte_count"] == 1
    assert report["selected_source_cell_bbox"] == {
        "min_inclusive": [7, 3],
        "max_inclusive": [7, 3],
        "cell_extent_max_exclusive": [8, 4],
    }
    assert report["selected_godot_world_cell_bbox"] == {
        "cell_extent_min_inclusive_xz": [-1.0, -1.0],
        "cell_extent_max_exclusive_xz": [0.0, 0.0],
    }
    components = report["selected_connected_components"]
    assert components["component_count"] == 1
    assert components["largest_component_size"] == 1
    p3 = report["canonical_p3_neighborhood_counts"]
    assert p3["half_extent_world_units"] == 64.0
    assert p3["neighborhoods"]["player"]["selected_cell_count"] == 0


def test_leaves_ties_underwater_sources_and_nonshore_material_two_unchanged(
    tmp_path: Path,
) -> None:
    patch = _flat_patch(0, 0, material=4, height=64)
    # Height columns 0..2 are water; cell x=3 is above water because its east
    # corners reach raw height 64. It touches water at x=2.
    for row in patch.height:
        row[0] = 32
        row[1] = 32
    patch.visual[4][3] = 0xF202
    patch.visual[3][3] = 0x1101
    patch.visual[5][3] = 0x2203
    # Mat2 inside the water-height region must not be considered land.
    patch.visual[0][0] = 0x3302
    # Above-water mat2 away from water is not a shore candidate.
    patch.visual[7][7] = 0x4402
    fixture = tmp_path / "unchanged.a3d"
    before = _write_fixture(fixture, [patch])

    output, report = tool.build_result(fixture)

    assert report["selected_cell_count"] == 0
    assert report["no_grass_majority"] == 1
    assert output == before
    assert report["input_sha256"] == report["prospective_output_sha256"]


def test_write_is_atomic_and_matches_prospective_digest(tmp_path: Path) -> None:
    land = _flat_patch(0, 0, material=4, height=64)
    water = _flat_patch(1, 0, material=0, height=32)
    land.visual[2][6] = 3
    land.visual[2][7] = 2
    fixture = tmp_path / "write.a3d"
    _write_fixture(fixture, [land, water], tail=b"unchanged-tail")
    output, report = tool.build_result(fixture)

    tool._atomic_replace(fixture, output)

    assert fixture.read_bytes() == output
    assert tool._sha256(fixture.read_bytes()) == report["prospective_output_sha256"]


def test_shared_edge_selection_uses_runtime_down_right_corner_owner(
    tmp_path: Path,
) -> None:
    left = _flat_patch(0, 0, material=4, height=32)
    right = _flat_patch(1, 0, material=4, height=64)
    # The left patch's duplicate east edge says water while the runtime global
    # corner lattice assigns x=8 to the right patch and therefore says land.
    # Candidate (7,3) touches canonical water at (6,3); its north/south grass
    # neighbors inherit the same runtime-owned high boundary and form a strict
    # mat1 majority. A local-patch edge implementation cannot select it.
    left.visual[2][7] = 1
    left.visual[3][7] = 0xCD02
    left.visual[4][7] = 1
    fixture = tmp_path / "mismatched-shared-edge.a3d"
    before = _write_fixture(fixture, [left, right], tail=b"unchanged-tail")

    output, report = tool.build_result(fixture)
    _, _, _, cells, _, _ = tool._read_cells(fixture)

    assert (
        tool._patch_local_cell_max_height_quarters(left, 7, 3)
        <= tool.WATER_SURFACE_HEIGHT_QUARTERS
    )
    assert cells[(7, 3)].max_height_quarters > tool.WATER_SURFACE_HEIGHT_QUARTERS
    assert report["selected_cell_count"] == 1
    assert report["replacement_counts"] == {"1": 1}
    corner_audit = report["corner_height_ownership_audit"]
    assert corner_audit["shared_edge_height_mismatch_count"] == 5
    assert (
        corner_audit["material_2_height_water_classification_disagreement_count"]
        == 1
    )
    offset = tool.fmt.A3DHeader.SIZE + tool.PATCH_VISUAL_OFFSET + (3 * 8 + 7) * 2
    assert output[offset] == 1
    assert output[offset + 1] == 0xCD
    assert output[:offset] == before[:offset]
    assert output[offset + 1 :] == before[offset + 1 :]


def test_shipped_successor_repeat_is_terminal_dry_run_and_write_noop() -> None:
    path = tool.SHIPPED_MAP_PATH
    before = path.read_bytes()
    before_stat = path.stat()
    assert tool._sha256(before) == tool.SHIPPED_SUCCESSOR_SHA256
    assert (
        tool._input_transaction_state(path, tool.SHIPPED_SOURCE_SHA256)
        == "source_snapshot"
    )

    output, dry_report = tool.build_result(path)
    write_report = tool.execute(path, write=True)
    after_stat = path.stat()

    assert output == before == path.read_bytes()
    assert dry_report["transaction_state"] == "already_applied"
    assert dry_report["selected_cell_count"] == 0
    assert dry_report["changed_byte_count"] == 0
    assert dry_report["prospective_output_sha256"] == tool.SHIPPED_SUCCESSOR_SHA256
    assert dry_report["shipped_transaction_contract"] == {
        "owner": "immutable_source_snapshot_one_pass",
        "source_sha256": tool.SHIPPED_SOURCE_SHA256,
        "successor_sha256": tool.SHIPPED_SUCCESSOR_SHA256,
        "source_selected_cells_sha256": (
            tool.SHIPPED_SOURCE_SELECTED_CELLS_SHA256
        ),
        "source_selected_cell_count": 1166,
    }
    assert write_report["mode"] == "write"
    assert write_report["transaction_state"] == "already_applied"
    assert write_report["write_performed"] is False
    assert write_report["written_sha256"] == tool.SHIPPED_SUCCESSOR_SHA256
    assert after_stat.st_ino == before_stat.st_ino
    assert after_stat.st_mtime_ns == before_stat.st_mtime_ns


def test_altered_shipped_map_hash_fails_closed_without_planning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    altered = _flat_patch(0, 0, material=2, height=64)
    fixture = tmp_path / "game_map_y8.a3d"
    _write_fixture(fixture, [altered])
    monkeypatch.setattr(tool, "SHIPPED_MAP_PATH", fixture.resolve())

    with pytest.raises(ValueError, match="outside the immutable one-pass contract"):
        tool.build_result(fixture)

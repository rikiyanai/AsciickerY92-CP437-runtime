#!/usr/bin/env python3
"""FL-4876. The linear realization vocabulary has exactly one owner.

The producer emits logical realization kinds from two functions.  The lowerer
that authors geometry enumerated only one of them, so every ``segment`` -- one
quarter of this lane's linear realizations -- reached asset authoring as an
unknown kind and stopped the build.  The tests here bind the two sides
together so the next added kind fails at import, not seventy minutes into a
build, and pin the run-length contract the segment geometry must honour.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pipeline.population_asset_geometry import (  # noqa: E402
    fence_panel,
)
from scripts.pipeline.compile_population_asset_plan import (  # noqa: E402
    _COVERED_LINEAR_KINDS,
    _extent,
    _linear_geometry,
)
from scripts.pipeline.population_representation_contract import (  # noqa: E402
    LINEAR_REALIZATION_KINDS,
    LINEAR_RUN_LENGTH_KINDS,
)

CONTRACT_SOURCE = (
    ROOT / "scripts" / "pipeline" / "population_representation_contract.py")

CROSS_SECTION = {
    "curb_height_m": 0.15,
    "carriageway_width_m": 5.5,
    "sidewalk_width_m": 2.0,
    "tactile_paving_width_m": 0.3,
    "drainage_gutter_width_m": 0.35,
    "rail_boundary_fence_height_m": 1.8,
}

# Every module this lane realizes as a segment, each with the variant it is
# actually dispatched with.  Read out of
# .run/numabe_connected/build_r1r8_01/plan/population/population_plan.linear.json
# rather than invented: a variant string the geometry does not know is a hard
# refusal, so a guessed one would test nothing but the guess.
SEGMENT_MODULES = [
    ("concrete_block_wall", "gray_block_boundary_wall"),
    ("concrete_block_wall", "gray_block_wall_with_tiled_coping_and_gate_piers"),
    ("concrete_block_wall", "cream_plaster_wall_with_blue_tile_coping"),
    ("concrete_retaining_edge", "railside_concrete_wall"),
    ("curb_and_open_gutter", "open_concrete_edge_channel"),
    ("curb_edge_line", "raised_curb_with_red_toned_walkway"),
    ("ivy_wall", "ivy_covered_boundary"),
    ("lattice_boundary_fence", "lattice_slatted_boundary_with_planting_strip"),
    ("mesh_fence", "metal_gate_with_boundary_wall"),
    ("metal_fence", "black_collapsible_accordion_gate"),
    ("metal_fence", "beige_corrugated_fence_with_black_gate"),
    ("metal_boundary_fence", "dark_vertical_rail"),
    ("open_and_covered_drain", "open_stone_channel"),
    ("pedestrian_guardrail_continuous", "gray_tube"),
    ("pedestrian_guardrail_vertical_bar_continuous", "galvanized_silver"),
    ("picket_fence", "white_picket_over_block_wall"),
    ("platform_railing", "station_platform_edge_gray"),
    ("platform_railing", "white_vertical_bar_platform_rear_fence"),
    ("rail_corridor_fence", "concrete_base_mesh_fence"),
    ("rail_corridor_mesh_fence", "vertical_mesh_boundary"),
    ("rail_track_corridor", "double_track_ballasted"),
    ("sidewalk_paver_surface", "gray_interlocking_with_tactile"),
    ("stone_cobble_wall", "irregular_dark_stone"),
    ("stone_lined_water_channel", "rounded_stone_embankment"),
    ("trimmed_hedge", "dense_broadleaf_hedge"),
    ("waterway_bridge_railing", "dark_weathered_rail"),
    ("waterway_bridge_railing", "small_concrete_footbridge_with_dark_railings"),
]


def _placement(module_id: str, variant: str | None) -> dict:
    return {
        "placement_id": f"test-{module_id}",
        "module_id": module_id,
        "variant": variant,
        "asset_contract": {"cross_section_m": dict(CROSS_SECTION)},
    }


def _segment(length_m: float) -> dict:
    return {
        "logical_instance_id": "test-placement:segment:0000",
        "kind": "segment",
        "pose": {"E": 0.0, "N": 0.0, "yaw_deg": 0.0},
        "length_m": float(length_m),
        "physical_envelope": {
            "anchor": "run_midpoint",
            "longitudinal_extent_m": float(length_m),
            "max_longitudinal_overhang_m": 0.0,
            "fit_policy": "inside_logical_run",
            "length_basis": "population_run_length",
        },
    }


# --------------------------------------------------------------------------
# The vocabulary has one owner.
# --------------------------------------------------------------------------

def test_every_kind_the_producer_emits_is_declared():
    """The check that would have caught the original drift.

    Both producer functions write their kind as a literal.  Scanning for the
    literals is what makes this test independent of the enumeration it
    guards -- reading the constant back would prove nothing.
    """
    source = CONTRACT_SOURCE.read_text(encoding="utf-8")
    emitted = set(re.findall(r'"kind":\s*"([a-z_]+)"', source))
    assert emitted, "no realization kind literals found in the producer"
    assert emitted <= LINEAR_REALIZATION_KINDS, (
        "producer emits kinds absent from LINEAR_REALIZATION_KINDS: "
        f"{sorted(emitted - LINEAR_REALIZATION_KINDS)}")


def test_the_declared_set_has_no_kind_no_producer_emits():
    source = CONTRACT_SOURCE.read_text(encoding="utf-8")
    emitted = set(re.findall(r'"kind":\s*"([a-z_]+)"', source))
    assert LINEAR_REALIZATION_KINDS <= emitted, (
        "LINEAR_REALIZATION_KINDS declares kinds no producer emits: "
        f"{sorted(LINEAR_REALIZATION_KINDS - emitted)}")


def test_segment_is_in_the_declared_vocabulary():
    assert "segment" in LINEAR_REALIZATION_KINDS
    assert LINEAR_RUN_LENGTH_KINDS == {"unit", "segment"}


def test_the_lowerer_covers_the_contract_exactly():
    assert _COVERED_LINEAR_KINDS == LINEAR_REALIZATION_KINDS


def test_an_uncovered_producer_kind_fails_at_import():
    """The guard must stop the pipeline, not decorate it."""
    script = (
        "import scripts.pipeline.population_representation_contract as c\n"
        "c.LINEAR_REALIZATION_KINDS = frozenset("
        "c.LINEAR_REALIZATION_KINDS | {'helix'})\n"
        "import scripts.pipeline.compile_population_asset_plan\n")
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT,
        capture_output=True, text=True, env={"PYTHONPATH": str(ROOT),
                                             "PATH": "/usr/bin:/bin"})
    assert result.returncode != 0, "an uncovered kind imported cleanly"
    assert "helix" in result.stderr
    assert "does not cover" in result.stderr


# --------------------------------------------------------------------------
# A segment is realized at its measured run length.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("module_id,variant", SEGMENT_MODULES)
def test_every_segment_module_authors_geometry(module_id, variant):
    vertices, faces, family, basis = _linear_geometry(
        _placement(module_id, variant), _segment(12.0), 1.0)
    assert vertices, f"{module_id} authored an empty segment mesh"
    assert faces
    assert family == module_id
    assert basis == "planned_linear_run_segment"


@pytest.mark.parametrize("module_id,variant", SEGMENT_MODULES)
@pytest.mark.parametrize("length_m", [1.0, 8.0, 40.0, 134.62])
def test_segment_extent_equals_the_declared_run_length(
        module_id, variant, length_m):
    """max_longitudinal_overhang_m is 0.0, so the mesh fills the run exactly.

    This is the gate that caught fence_panel: its end bars straddled x=+-L/2
    and overhung the placement by a half-width at each end.
    """
    vertices, _faces, _family, _basis = _linear_geometry(
        _placement(module_id, variant), _segment(length_m), 1.0)
    longitudinal = _extent(vertices, 1.0)[0]
    assert longitudinal == pytest.approx(length_m, abs=1e-6), (
        f"{module_id} at {length_m} m spans {longitudinal} m; the declared "
        "envelope allows no longitudinal overhang")


@pytest.mark.parametrize("style", ["bars", "pickets"])
@pytest.mark.parametrize("length_m", [0.5, 1.0, 8.0, 60.0])
def test_fence_panel_never_overhangs_its_span(style, length_m):
    vertices: list[tuple] = []
    faces: list[list[int]] = []
    fence_panel(vertices, faces, length_m, 1.2, (90, 95, 100), 1.0, style)
    xs = [float(vertex[0]) for vertex in vertices]
    assert min(xs) >= -length_m / 2 - 1e-9
    assert max(xs) <= length_m / 2 + 1e-9


def test_small_concrete_footbridge_has_deck_and_two_railings():
    vertices, faces, _family, _basis = _linear_geometry(
        _placement("waterway_bridge_railing",
                   "small_concrete_footbridge_with_dark_railings"),
        _segment(4.2), 1.0)
    assert faces
    assert _extent(vertices, 1.0) == pytest.approx((4.2, 2.7, 1.13))
    assert {(151, 149, 140), (72, 68, 63)} <= {
        tuple(vertex[6:9]) for vertex in vertices}


def test_temple_precinct_wall_is_cream_plaster_with_blue_coping():
    vertices, faces, _family, _basis = _linear_geometry(
        _placement("concrete_block_wall", "cream_plaster_wall_with_blue_tile_coping"),
        _segment(8.0), 1.0)
    assert faces
    assert _extent(vertices, 1.0) == pytest.approx((8.0, 0.48, 1.70))
    rgbs = {tuple(v[6:9]) for v in vertices}
    assert (228, 220, 203) in rgbs and (43, 84, 113) in rgbs  # plaster body and blue coping


def test_tiled_block_wall_keeps_coping_and_gate_piers():
    vertices, faces, _family, _basis = _linear_geometry(
        _placement("concrete_block_wall",
                   "gray_block_wall_with_tiled_coping_and_gate_piers"),
        _segment(8.0), 1.0)
    assert faces
    assert _extent(vertices, 1.0) == pytest.approx((8.0, 0.62, 1.70))
    assert {(151, 151, 145), (105, 106, 103), (117, 72, 64)} <= {
        tuple(vertex[6:9]) for vertex in vertices}


def test_authored_metal_gate_variants_are_not_the_generic_bar_fence():
    accordion_v, accordion_f, _family, _basis = _linear_geometry(
        _placement("metal_fence", "black_collapsible_accordion_gate"),
        _segment(2.7), 1.0)
    corrugated_v, corrugated_f, _family, _basis = _linear_geometry(
        _placement("metal_fence", "beige_corrugated_fence_with_black_gate"),
        _segment(4.2), 1.0)
    generic_v, generic_f, _family, _basis = _linear_geometry(
        _placement("metal_fence", "galvanized_metal_gate"),
        _segment(4.2), 1.0)

    accordion_colours = {tuple(vertex[6:9]) for vertex in accordion_v}
    corrugated_colours = {tuple(vertex[6:9]) for vertex in corrugated_v}
    assert accordion_colours == {(35, 38, 38)}
    assert {(184, 176, 148), (151, 145, 124),
            (48, 48, 45), (62, 62, 57)} <= corrugated_colours
    assert len(accordion_f) != len(generic_f)
    assert len(corrugated_f) != len(generic_f)


def test_frontage_drain_stays_open_and_hedge_variants_keep_observed_heights():
    open_v, open_f, _family, _basis = _linear_geometry(
        _placement("open_and_covered_drain", "open_stone_channel"),
        _segment(4.0), 1.0)
    covered_v, covered_f, _family, _basis = _linear_geometry(
        _placement("open_and_covered_drain",
                   "covered_edge_channel_with_drain_cover"),
        _segment(4.0), 1.0)
    tall_v, _faces, _family, _basis = _linear_geometry(
        _placement("trimmed_hedge", "tall_dense_boundary_hedge"),
        _segment(7.5), 1.0)
    channel_v, _faces, _family, _basis = _linear_geometry(
        _placement("trimmed_hedge", "dense_hedge_with_open_channel"),
        _segment(7.2), 1.0)

    # The open source view must not silently receive the solid cover slab.
    assert len(open_f) < len(covered_f)
    assert _extent(open_v, 1.0)[1] == pytest.approx(0.70)
    assert _extent(tall_v, 1.0)[2] == pytest.approx(1.60)
    assert _extent(channel_v, 1.0)[2] == pytest.approx(1.35)


def test_unit_and_segment_are_one_geometry_vocabulary():
    """Same module, same length, two kinds -- the asset must be identical.

    If these ever diverge the two kinds are no longer one vocabulary and the
    shared-enumeration claim above is false.
    """
    placement = _placement("pedestrian_guardrail_continuous", "gray_tube")
    segment = _segment(9.0)
    unit = dict(segment, kind="unit",
                logical_instance_id="test-placement:unit")
    seg_v, seg_f, _fam, seg_basis = _linear_geometry(placement, segment, 1.0)
    unit_v, unit_f, _fam2, unit_basis = _linear_geometry(placement, unit, 1.0)
    assert seg_v == unit_v
    assert seg_f == unit_f
    assert seg_basis == "planned_linear_run_segment"
    assert unit_basis == "planned_linear_unit"


def test_a_segment_without_a_length_is_refused():
    placement = _placement("sidewalk_paver_surface", None)
    logical = {"logical_instance_id": "test:segment:0000", "kind": "segment",
               "pose": {"E": 0.0, "N": 0.0, "yaw_deg": 0.0}}
    with pytest.raises(ValueError, match="has no length"):
        _linear_geometry(placement, logical, 1.0)


def test_an_unknown_kind_is_still_refused():
    placement = _placement("sidewalk_paver_surface", None)
    logical = dict(_segment(4.0), kind="helix")
    with pytest.raises(ValueError, match="unknown linear logical kind"):
        _linear_geometry(placement, logical, 1.0)


# --------------------------------------------------------------------------
# The same drift, two more doors.  Both of these were latent behind the
# segment failure: the build died before either could run.
# --------------------------------------------------------------------------

def test_the_point_builder_covers_every_point_realizable_module():
    from scripts.pipeline.population_asset_geometry import (
        _COVERED_POINT_MODULES)
    from scripts.pipeline.population_representation_contract import (
        POINT_REALIZABLE_MODULES)
    assert _COVERED_POINT_MODULES == POINT_REALIZABLE_MODULES


def test_a_run_expanded_bollard_is_point_realizable():
    """linear_kind calls it a unit; a bollard on a run is expanded to points.

    It is the one module that produces point realizations without being a
    LINEAR_POINT_MODULE, and it accounts for 3869 of this lane's 3913 point
    realizations.
    """
    from scripts.pipeline.population_representation_contract import (
        LINEAR_POINT_MODULES, LINEAR_RUN_POINT_MODULES,
        POINT_REALIZABLE_MODULES, linear_kind)
    assert linear_kind("bollard_curb_island") == "unit"
    assert "bollard_curb_island" not in LINEAR_POINT_MODULES
    assert "bollard_curb_island" in LINEAR_RUN_POINT_MODULES
    assert "bollard_curb_island" in POINT_REALIZABLE_MODULES


@pytest.mark.parametrize(
    "variant", ["chevron_marked_bollard", "gray_stone_bollard"])
def test_a_run_expanded_bollard_authors_a_point_mesh(variant):
    placement = _placement("bollard_curb_island", variant)
    logical = {"logical_instance_id": "test:point:0000", "kind": "point",
               "pose": {"E": 0.0, "N": 0.0, "yaw_deg": 0.0}}
    vertices, faces, family, basis = _linear_geometry(placement, logical, 1.0)
    assert vertices and faces
    assert family == "bollard_curb_island"
    assert basis == "planned_point_module"


def test_bollard_point_and_unit_author_the_same_mesh():
    """The point path must reach the mesh already authored for the unit path.

    _bollard_variant_mesh takes no length, so the two paths cannot diverge
    unless someone authors a second bollard.
    """
    placement = _placement("bollard_curb_island", "chevron_marked_bollard")
    point = {"logical_instance_id": "test:point:0000", "kind": "point",
             "pose": {"E": 0.0, "N": 0.0, "yaw_deg": 0.0}}
    point_v, point_f, _fam, _b = _linear_geometry(placement, point, 1.0)
    unit_v, unit_f, _fam2, _b2 = _linear_geometry(
        placement, dict(_segment(4.0), kind="unit"), 1.0)
    assert point_v == unit_v
    assert point_f == unit_f


def test_the_span_builder_can_import_its_own_maths():
    """catenary_boxes calls math.hypot; the module never imported math.

    Nothing caught it because every span on this lane sat behind the segment
    failure, so the span branch had never executed in a real run.
    """
    from scripts.pipeline import population_asset_geometry
    assert hasattr(population_asset_geometry, "math")
    vertices: list[tuple] = []
    faces: list[list[int]] = []
    population_asset_geometry.catenary_boxes(
        vertices, faces, -10.0, 10.0, 5.0, 5.0, (48, 52, 52), 1.0)
    assert vertices and faces

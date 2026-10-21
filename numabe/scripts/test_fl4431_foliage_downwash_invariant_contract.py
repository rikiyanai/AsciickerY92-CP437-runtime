#!/usr/bin/env python3
# =============================================================================
# test_fl4431_foliage_downwash_invariant_contract.py
# =============================================================================
# Product evaluates eight authored foliage blades for each nearby grass anchor.
# Their scatter admission is slot-specific, but their world anchor, quantized
# animation time, and Nausicaa downwash lookup are identical. Repeating the
# local-light scan inside every admitted slot spent up to eight times the work
# without producing a different value.
#
# This contract protects the exact loop-invariant move:
#   1. establish all eight slot admissions;
#   2. skip an anchor when none are admitted;
#   3. materialize one anchor time and downwash fact;
#   4. pass that immutable fact through the unchanged blade wind law.
#
# The executable fixtures compare the old per-slot evaluation with the new
# per-anchor evaluation at float32 granularity. They include dense and sparse
# grass, dirt-edge foliage, the flower slot, quantized-time boundaries, multiple
# downwash rows, and anchors whose facts must never alias.
#
# Run directly:
#   python3 scripts/test_fl4431_foliage_downwash_invariant_contract.py
# Run through pytest:
#   python3 -m pytest scripts/test_fl4431_foliage_downwash_invariant_contract.py -q
# =============================================================================

"""Guard the exact FL-4431 foliage downwash loop-invariant materialization."""

from __future__ import annotations
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


import math
import re
import shutil
import struct
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
PRODUCT_PATH = REPO / "godot_project" / "render" / "product" / "cell_owner_product.glsl"


def _strip_glsl_comments(source: str) -> str:
    """Remove prose so explanatory text cannot satisfy an executable law."""

    without_blocks = re.sub(r"/\*.*?\*/", " ", source, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", " ", without_blocks)


def _compact(source: str) -> str:
    """Collapse formatting while preserving every executable token."""

    return re.sub(r"\s+", "", source)


def _extract_function(source: str, function_name: str) -> str | None:
    """Return one complete GLSL function using balanced braces."""

    signature = re.search(
        r"\b\w+(?:\s+\w+)*\s+"
        + re.escape(function_name)
        + r"\s*\([^;]*?\)\s*\{",
        source,
        re.DOTALL,
    )
    if signature is None:
        return None
    opening = source.find("{", signature.start())
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[signature.start() : index + 1]
    return None


def _slot_loop_ranges(source: str) -> tuple[tuple[int, int], ...]:
    """Return balanced ranges for the two authored eight-slot loops."""

    header = re.compile(
        r"for\s*\(\s*int\s+slot\s*=\s*0\s*;\s*"
        r"slot\s*<\s*FOLIAGE_SLOTS_PER_GRASS_CELL\s*;\s*slot\+\+\s*\)"
    )
    ranges: list[tuple[int, int]] = []
    for match in header.finditer(source):
        opening = source.find("{", match.end())
        if opening < 0:
            continue
        depth = 0
        for index in range(opening, len(source)):
            if source[index] == "{":
                depth += 1
            elif source[index] == "}":
                depth -= 1
                if depth == 0:
                    ranges.append((match.start(), index + 1))
                    break
    return tuple(ranges)


def _brace_depth_at(source: str, position: int) -> int:
    """Return lexical brace depth immediately before one source position."""

    depth = 0
    for character in source[:position]:
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
    return depth


def contract_violations(product_source: str) -> tuple[str, ...]:
    """Return every broken source-shape law for the invariant hoist."""

    violations: list[str] = []
    product = _strip_glsl_comments(product_source)
    wind = _extract_function(product, "foliage_wind_state")
    fact = _extract_function(product, "foliage_anchor_wind_fact")
    blade = _extract_function(product, "foliage_blade_from_anchor")
    coverage = _extract_function(product, "foliage_blade_coverage_at_cell")
    target_admission = _extract_function(
        product, "product_foliage_target_admitted_at_cell"
    )
    if wind is None:
        violations.append("foliage_wind_state is missing")
    if fact is None:
        violations.append("foliage_anchor_wind_fact is missing")
    if blade is None:
        violations.append("foliage_blade_from_anchor is missing")
    if coverage is None:
        violations.append("foliage_blade_coverage_at_cell is missing")
    if violations:
        return tuple(violations)

    assert wind is not None
    assert fact is not None
    assert blade is not None
    assert coverage is not None
    wind_compact = _compact(wind)
    fact_compact = _compact(fact)
    blade_compact = _compact(blade)
    coverage_compact = _compact(coverage)

    if "foliage_downwash_boost(" in wind:
        violations.append(
            "the eight-slot wind path must not resurrect a local-light scan"
        )
    if fact_compact.count("foliage_downwash_boost(") != 1:
        violations.append(
            "one anchor fact must perform exactly one downwash lookup"
        )
    quantized_time = (
        "floatframerate=5.0;"
        "floatlocation_seed=stable_grid_noise(cell_xz,1.0,113u);"
        "floatphase=mod(10.0*location_seed,1.0/framerate);"
        "fact.quantized_time="
        "floor((params.feature_params.x+phase)*framerate)/framerate;"
    )
    if quantized_time not in fact_compact:
        violations.append(
            "anchor time must retain the established float-expression order"
        )
    if "floatdownwash_boost,vec2downwash_direction" not in wind_compact:
        violations.append(
            "wind must consume the anchor-owned downwash value and direction"
        )
    if "vec2dw_dir=downwash_direction;floatdw=downwash_boost;" not in wind_compact:
        violations.append(
            "the existing downwash visual law must receive the carried fact"
        )
    if "boolslot_admitted,FoliageAnchorWindFactanchor_wind" not in blade_compact:
        violations.append(
            "blade materialization must receive admission plus the anchor fact"
        )
    if "if(!slot_admitted)returnb;" not in blade_compact:
        violations.append(
            "a non-admitted slot must retain its fail-closed empty blade"
        )
    if "foliage_scatter_accept_anchor(" in blade_compact:
        violations.append(
            "slot admission must not be recomputed inside blade materialization"
        )
    if "floatt=anchor_wind.quantized_time;" not in blade_compact:
        violations.append(
            "every slot must consume the anchor-owned quantized time unchanged"
        )
    carried_call = (
        "anchor_wind.downwash_boost,anchor_wind.downwash_direction"
    )
    if carried_call not in blade_compact:
        violations.append(
            "every admitted blade must carry both downwash fact components"
        )

    exact_admission = (
        "foliage_scatter_accept_anchor("
        "anchor,slot,anchor_facts,anchor_scatter_density)"
    )
    admission = coverage_compact.find(
        "admitted_slots[slot]=" + exact_admission
    )
    guard = coverage_compact.find("if(!anchor_admitted)continue;")
    materialize = coverage_compact.find(
        "foliage_anchor_wind_fact(anchor_facts.world_origin.xz)"
    )
    blade_call = coverage_compact.find("foliage_blade_from_anchor(")
    if min(admission, guard, materialize, blade_call) < 0:
        violations.append(
            "coverage must contain admission, empty-anchor guard, fact, and blade call"
        )
    elif not admission < guard < materialize < blade_call:
        violations.append(
            "coverage must admit slots before one fact and before blade evaluation"
        )
    if coverage_compact.count("foliage_anchor_wind_fact(") != 1:
        violations.append(
            "coverage must materialize one downwash fact per admitted anchor"
        )
    if coverage_compact.count(exact_admission) != 1:
        violations.append(
            "slot admission must use anchor, slot, anchor facts, and anchor density"
        )

    slot_loops = _slot_loop_ranges(coverage)
    fact_statement = coverage.find("FoliageAnchorWindFact anchor_wind")
    if len(slot_loops) != 2:
        violations.append(
            "coverage must retain one admission loop plus one blade loop"
        )
    elif fact_statement < 0:
        violations.append("the anchor fact declaration is missing")
    else:
        first_start, first_end = slot_loops[0]
        second_start, second_end = slot_loops[1]
        fact_depth = _brace_depth_at(coverage, fact_statement)
        second_header_depth = _brace_depth_at(coverage, second_start)
        if not first_start < first_end < fact_statement < second_start:
            violations.append(
                "anchor fact must follow the complete admission loop and precede the blade loop"
            )
        if second_start <= fact_statement < second_end:
            violations.append(
                "anchor fact must remain outside the second slot-loop body"
            )
        if fact_depth != second_header_depth:
            violations.append(
                "anchor fact and second slot-loop header must share one brace depth"
            )
    if "FOLIAGE_ROLE_FLOWER" not in coverage:
        violations.append("flower winner grammar must remain in the coverage owner")
    # FL-4722 extracts the shared target admission so both the legacy mixed-
    # cohort path and the bounded ANSI traversal consume the same dirt-edge
    # owner. The invariant is presence in that shared helper, not textual
    # nesting inside one caller.
    dirt_owner = target_admission if target_admission is not None else coverage
    if "TERRAIN_FAMILY_DIRT" not in dirt_owner:
        violations.append("dirt-edge admission must remain in the coverage owner")
    if "FOLIAGE_SHELL_LAYER_COUNT" not in coverage:
        violations.append("dense shell evaluation must remain in the coverage owner")
    return tuple(violations)


def _f32(value: float) -> float:
    """Round one intermediate exactly to IEEE-754 binary32."""

    return struct.unpack("<f", struct.pack("<f", value))[0]


def _bits(value: float) -> int:
    """Return the binary32 payload used for strict parity assertions."""

    return struct.unpack("<I", struct.pack("<f", _f32(value)))[0]


def _vec_bits(values: tuple[float, ...]) -> tuple[int, ...]:
    """Return every float32 payload in one modeled blade result."""

    return tuple(_bits(value) for value in values)


@dataclass(frozen=True)
class DownwashRow:
    """One CPU-side mirror of the Product local-light lanes used by downwash."""

    kind: int
    world_x: float
    world_z: float
    radius: float
    intensity: float


@dataclass(frozen=True)
class AnchorFixture:
    """Inputs that distinguish anchor identity, admission, role, and time."""

    name: str
    surface_family: str
    cell_x: float
    cell_z: float
    location_seed: float
    feature_time: float
    admitted: tuple[bool, ...]
    roles: tuple[str, ...]
    rows: tuple[DownwashRow, ...]


def _quantized_time(feature_time: float, location_seed: float) -> float:
    """Mirror the shader expression with a float32 write after each operation."""

    framerate = _f32(5.0)
    phase = _f32(
        math.fmod(
            _f32(_f32(10.0) * _f32(location_seed)),
            _f32(_f32(1.0) / framerate),
        )
    )
    shifted = _f32(_f32(feature_time) + phase)
    bucket = math.floor(_f32(shifted * framerate))
    return _f32(_f32(float(bucket)) / framerate)


def _downwash(
    cell_x: float,
    cell_z: float,
    quantized_time: float,
    rows: tuple[DownwashRow, ...],
) -> tuple[float, float, float, int]:
    """Mirror the first-admitted downwash lookup and count inspected rows."""

    inspected = 0
    for row in rows:
        inspected += 1
        if row.kind != 5:
            continue
        dx = _f32(_f32(cell_x) - _f32(row.world_x))
        dz = _f32(_f32(cell_z) - _f32(row.world_z))
        distance = _f32(math.sqrt(_f32(_f32(dx * dx) + _f32(dz * dz))))
        radius = max(_f32(row.radius), _f32(0.001))
        if distance > radius:
            continue
        fall = _f32(max(0.0, min(1.0, _f32(1.0 - _f32(distance / radius)))))
        phase = _f32(
            _f32(distance / radius)
            - _f32(
                _f32(quantized_time * _f32(1.5))
                - math.floor(_f32(quantized_time * _f32(1.5)))
            )
        )
        ring = _f32(max(0.0, min(1.0, _f32(1.0 - abs(phase)))))
        amplitude = _f32(
            _f32(row.intensity)
            * fall
            * _f32(_f32(0.5) + _f32(_f32(0.5) * ring))
        )
        amplitude = _f32(max(0.0, min(1.0, amplitude)))
        if distance > _f32(0.01):
            return (
                amplitude,
                _f32(dx / distance),
                _f32(dz / distance),
                inspected,
            )
        return (amplitude, _f32(0.0), _f32(1.0), inspected)
    return (_f32(0.0), _f32(0.0), _f32(1.0), inspected)


def _slot_output(
    quantized_time: float,
    downwash: tuple[float, float, float, int],
    slot: int,
    role: str,
    cell_x: float,
    cell_z: float,
) -> tuple[int | str, ...]:
    """Model the unchanged downwash branch plus role identity for one blade."""

    boost, direction_x, direction_z, _ = downwash
    seed1 = _f32((slot + 1) * 0.071)
    seed2 = _f32((slot + 1) * 0.113)
    ambient_x = _f32(0.330 + seed1 * 0.10)
    ambient_z = _f32(0.944 - seed2 * 0.05)
    ambient_len = _f32(
        math.sqrt(
            _f32(_f32(ambient_x * ambient_x) + _f32(ambient_z * ambient_z))
        )
    )
    ambient_x = _f32(ambient_x / ambient_len)
    ambient_z = _f32(ambient_z / ambient_len)
    wind_sample = _f32(_f32(seed1 * seed2 + _f32(0.365)) * _f32(2.0) - 1.0)
    world_sway = _f32(wind_sample * _f32(math.radians(60.0)))
    view_phase = _f32(
        _f32(
            _f32(quantized_time + seed1 + _f32(seed2 * _f32(0.37)))
            * _f32(0.1)
        )
        * _f32(6.2831853)
    )
    view_sway = _f32(math.sin(view_phase) * _f32(math.radians(10.0)))
    if boost > _f32(0.0001):
        mixed_x = _f32(ambient_x * _f32(1.0 - boost) + direction_x * boost)
        mixed_z = _f32(ambient_z * _f32(1.0 - boost) + direction_z * boost)
        mixed_len = _f32(
            math.sqrt(_f32(_f32(mixed_x * mixed_x) + _f32(mixed_z * mixed_z)))
        )
        ambient_x = _f32(mixed_x / mixed_len)
        ambient_z = _f32(mixed_z / mixed_len)
        flutter_phase = _f32(
            _f32(quantized_time * _f32(22.0))
            + _f32(_f32(cell_x) * _f32(0.7))
            + _f32(_f32(cell_z) * _f32(0.9))
        )
        flutter = _f32(math.sin(flutter_phase))
        extra = _f32(
            _f32(boost * _f32(math.radians(70.0)))
            * _f32(_f32(0.6) + _f32(_f32(0.4) * flutter))
        )
        world_sway = _f32(world_sway + extra)
        wind_sample = _f32(max(-1.0, min(1.0, _f32(wind_sample + boost))))
    return (
        slot,
        role,
        *_vec_bits(
            (
                quantized_time,
                boost,
                direction_x,
                direction_z,
                ambient_x,
                ambient_z,
                wind_sample,
                world_sway,
                view_sway,
            )
        ),
    )


def _old_reference(
    fixture: AnchorFixture,
) -> tuple[tuple[tuple[int | str, ...], ...], int]:
    """Evaluate time and downwash independently inside every admitted slot."""

    outputs: list[tuple[int | str, ...]] = []
    row_inspections = 0
    for slot, admitted in enumerate(fixture.admitted):
        if not admitted:
            continue
        quantized_time = _quantized_time(
            fixture.feature_time, fixture.location_seed
        )
        downwash = _downwash(
            fixture.cell_x, fixture.cell_z, quantized_time, fixture.rows
        )
        row_inspections += downwash[3]
        outputs.append(
            _slot_output(
                quantized_time,
                downwash,
                slot,
                fixture.roles[slot],
                fixture.cell_x,
                fixture.cell_z,
            )
        )
    return tuple(outputs), row_inspections


def _new_materialization(
    fixture: AnchorFixture,
) -> tuple[tuple[tuple[int | str, ...], ...], int]:
    """Evaluate the shared fact once only when the anchor admits a blade."""

    if not any(fixture.admitted):
        return (), 0
    quantized_time = _quantized_time(
        fixture.feature_time, fixture.location_seed
    )
    downwash = _downwash(
        fixture.cell_x, fixture.cell_z, quantized_time, fixture.rows
    )
    outputs = tuple(
        _slot_output(
            quantized_time,
            downwash,
            slot,
            fixture.roles[slot],
            fixture.cell_x,
            fixture.cell_z,
        )
        for slot, admitted in enumerate(fixture.admitted)
        if admitted
    )
    return outputs, downwash[3]


def _fixtures() -> tuple[AnchorFixture, ...]:
    """Return dense, sparse, edge, flower, and boundary regression fixtures."""

    roles = (
        "root",
        "stem",
        "half_stem",
        "leaf_left",
        "leaf_right",
        "tip",
        "flower",
        "fill",
    )
    no_downwash = (
        DownwashRow(2, 0.0, 0.0, 1.0, 0.5),
        DownwashRow(3, 0.0, 0.0, 1.0, 0.5),
    )
    late_downwash = (
        DownwashRow(2, -4.0, 1.0, 3.0, 0.5),
        DownwashRow(3, 8.0, -2.0, 4.0, 0.7),
        DownwashRow(5, 4.0, 6.0, 7.0, 0.82),
    )
    multiple_downwash = (
        DownwashRow(5, -2.0, 4.0, 6.0, 0.61),
        DownwashRow(5, -1.5, 4.0, 6.0, 0.94),
    )
    return (
        AnchorFixture(
            "grass_dense_with_flower",
            "grass",
            5.0,
            7.0,
            0.03125,
            1.1999999,
            (True,) * 8,
            roles,
            late_downwash,
        ),
        AnchorFixture(
            "grass_sparse_boundary_below",
            "grass",
            -3.0,
            4.0,
            0.019999,
            0.1999999,
            (False, True, False, False, False, False, True, False),
            roles,
            multiple_downwash,
        ),
        AnchorFixture(
            "dirt_edge_boundary_exact",
            "dirt",
            -1.0,
            -2.0,
            0.0,
            0.2,
            (False, False, True, False, True, False, False, False),
            roles,
            no_downwash,
        ),
        AnchorFixture(
            "grass_boundary_above",
            "grass",
            12.0,
            -9.0,
            0.10001,
            0.2000001,
            (True, False, False, False, False, False, False, True),
            roles,
            late_downwash,
        ),
        AnchorFixture(
            "empty_anchor_skips_scan",
            "grass",
            4.0,
            6.0,
            0.03125,
            1.2,
            (False,) * 8,
            roles,
            late_downwash,
        ),
    )


def test_old_reference_parity() -> None:
    """Every admitted blade must retain bitwise-identical modeled wind output."""

    for fixture in _fixtures():
        old_outputs, old_inspections = _old_reference(fixture)
        new_outputs, new_inspections = _new_materialization(fixture)
        assert new_outputs == old_outputs, fixture.name
        admitted_count = sum(fixture.admitted)
        if admitted_count == 0:
            assert old_inspections == 0
            assert new_inspections == 0
        else:
            assert old_inspections == new_inspections * admitted_count
            assert new_inspections <= len(fixture.rows)


def test_cross_anchor_facts_do_not_alias() -> None:
    """Different anchors must produce independently distinguishable facts."""

    fixtures = _fixtures()
    first_outputs, _ = _new_materialization(fixtures[0])
    second_outputs, _ = _new_materialization(fixtures[1])
    assert first_outputs
    assert second_outputs
    first_fact = first_outputs[0][2:6]
    second_fact = second_outputs[0][2:6]
    assert first_fact != second_fact
    aliased_second = tuple(
        (*output[:2], *first_fact, *output[6:]) for output in second_outputs
    )
    assert aliased_second != second_outputs


def test_source_contract_and_destructive_mutants() -> None:
    """The real source passes and each realistic regression is rejected."""

    source = _expand_includes(PRODUCT_PATH.read_text(encoding="utf-8"))
    assert contract_violations(source) == ()
    fact_before_loop = (
        "\t\t\t\tFoliageAnchorWindFact anchor_wind =\n"
        "\t\t\t\t\tfoliage_anchor_wind_fact("
        "anchor_facts.world_origin.xz);\n\n"
        "\t\t\t\tfor (int slot = 0; "
        "slot < FOLIAGE_SLOTS_PER_GRASS_CELL; slot++) {\n"
    )
    fact_inside_loop = (
        "\t\t\t\tfor (int slot = 0; "
        "slot < FOLIAGE_SLOTS_PER_GRASS_CELL; slot++) {\n"
        "\t\t\t\t\tFoliageAnchorWindFact anchor_wind =\n"
        "\t\t\t\t\t\tfoliage_anchor_wind_fact("
        "anchor_facts.world_origin.xz);\n"
    )
    mutants = {
        "scan_resurrection": source.replace(
            "vec2 dw_dir = downwash_direction;\n\tfloat dw = downwash_boost;",
            "vec2 dw_dir = vec2(0.0);"
            "\n\tfloat dw = foliage_downwash_boost(cell_xz, t, dw_dir);",
            1,
        ),
        "slot_dependent_time": source.replace(
            "float t = anchor_wind.quantized_time;",
            "float t = anchor_wind.quantized_time + float(slot) * 0.2;",
            1,
        ),
        "missing_anchor_admission": source.replace(
            "\t\t\t\tif (!anchor_admitted) continue;\n",
            "",
            1,
        ),
        "cross_anchor_reuse": source.replace(
            "foliage_anchor_wind_fact(anchor_facts.world_origin.xz);",
            "foliage_anchor_wind_fact(target_facts.world_origin.xz);",
            1,
        ),
        "fact_moved_into_slot_loop": source.replace(
            fact_before_loop,
            fact_inside_loop,
            1,
        ),
        "wrong_admission_argument": source.replace(
            "anchor, slot, anchor_facts, anchor_scatter_density);",
            "anchor, slot, target_facts, anchor_scatter_density);",
            1,
        ),
    }
    for name, mutant in mutants.items():
        assert mutant != source, f"{name} mutant failed to alter the source"
        assert contract_violations(mutant), f"{name} mutant survived"


def test_glslang_vulkan11() -> None:
    """Compile Product through the CPU-only Vulkan 1.1 shader front end."""

    compiler = shutil.which("glslangValidator")
    assert compiler is not None, "glslangValidator is required"
    source = _expand_includes(PRODUCT_PATH.read_text(encoding="utf-8"))
    lines = source.splitlines()
    if lines and lines[0].strip() == "#[compute]":
        lines = lines[1:]
    with tempfile.TemporaryDirectory(
        prefix="fl4431_foliage_downwash_"
    ) as temp_dir:
        shader = Path(temp_dir) / "cell_owner_product.comp"
        output = Path(temp_dir) / "cell_owner_product.spv"
        shader.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = subprocess.run(
            [
                compiler,
                "-S",
                "comp",
                "--target-env",
                "vulkan1.1",
                "-o",
                str(output),
                str(shader),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    assert result.returncode == 0, result.stdout + result.stderr


def main() -> int:
    """Run the same tests used by pytest and print one stable receipt."""

    test_old_reference_parity()
    test_cross_anchor_facts_do_not_alias()
    test_source_contract_and_destructive_mutants()
    test_glslang_vulkan11()
    print(
        "FL4431_FOLIAGE_DOWNWASH_INVARIANT_CONTRACT_PASS "
        f"fixtures={len(_fixtures())} mutants=6"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

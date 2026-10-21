#!/usr/bin/env python3
"""FL-4431 Product shadow-bound traversal contract.

The Product shadow law combines three caster families:

* retained-static traversal yields either the ambient ratio or 1.0;
* actor and dynamic-mover scans max-combine their densities;
* the darker of retained-static and actor/dynamic attenuation wins.

The shipping implementation preserves those exact binary32 result bits while
changing traversal order at the proven 0.38 lower bound.  Ratios at or below
0.38 stay static-first because a static hit is immediately final.  Higher
ratios compute actor/dynamic attenuation first because a blob value no brighter
than the ratio is immediately final.  A high-ratio value still scans retained
static geometry when the blob is brighter, while ratio 1.0 never needs that
scan.

This contract executes the complete reviewed binary32 boundary matrix for both
projection families, checks exact output bits and traversal counts, compiles
ten destructive source mutations, then proves every mutation makes the source
contract red.  It is structural and arithmetic evidence only; it does not
establish target-runtime timing, visible correctness, or operator acceptance.
"""

from __future__ import annotations
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parent.parent
PRODUCT_PATH = ROOT / "godot_project" / "render" / "product" / "cell_owner_product.glsl"

BLOB_LOWER_BOUND = struct.unpack("<f", struct.pack("<f", 0.38))[0]
ATTENUATION_FLOOR = struct.unpack("<f", struct.pack("<f", 0.08))[0]
ONE = 1.0
ZERO = 0.0

ORTHOGRAPHIC = "orthographic"
PERSPECTIVE = "perspective"
PROJECTIONS = (ORTHOGRAPHIC, PERSPECTIVE)


def f32(value: float) -> float:
    """Round one value exactly as a Vulkan shader float does."""

    return struct.unpack("<f", struct.pack("<f", float(value)))[0]


def float_bits(value: float) -> int:
    """Return the exact binary32 representation of one value."""

    return struct.unpack("<I", struct.pack("<f", f32(value)))[0]


def bits_float(bits: int) -> float:
    """Interpret one unsigned word as a binary32 value."""

    return struct.unpack("<f", struct.pack("<I", bits))[0]


def next_up_f32(value: float) -> float:
    """Return the next representable binary32 value above a non-negative input."""

    rounded = f32(value)
    bits = float_bits(rounded)
    return bits_float(bits + 1 if rounded >= 0.0 else bits - 1)


def next_down_f32(value: float) -> float:
    """Return the next representable binary32 value below a positive input."""

    rounded = f32(value)
    bits = float_bits(rounded)
    return bits_float(bits - 1 if rounded > 0.0 else bits + 1)


def fadd(left: float, right: float) -> float:
    return f32(f32(left) + f32(right))


def fsub(left: float, right: float) -> float:
    return f32(f32(left) - f32(right))


def fmul(left: float, right: float) -> float:
    return f32(f32(left) * f32(right))


def fdiv(left: float, right: float) -> float:
    return f32(f32(left) / f32(right))


def fmin(left: float, right: float) -> float:
    left = f32(left)
    right = f32(right)
    return left if left <= right else right


def fmax(left: float, right: float) -> float:
    left = f32(left)
    right = f32(right)
    return left if left >= right else right


def clamp_f32(value: float, lower: float, upper: float) -> float:
    return fmin(fmax(value, lower), upper)


def mix_f32(left: float, right: float, weight: float) -> float:
    """Expand GLSL mix in the shipping source's binary32 operation order."""

    return fadd(
        fmul(left, fsub(ONE, weight)),
        fmul(right, weight),
    )


@dataclass(frozen=True)
class ReceiverFact:
    """One immutable receiver state consumed by the shadow composition."""

    projection: str
    normal: tuple[float, float, float]
    owner_kind: int
    face_index: int
    ambient_value: float
    lit_value: float
    static_hit: bool
    actor_density: float
    dynamic_density: float


@dataclass(frozen=True)
class Work:
    """Count the three expensive discovery families, not scalar arithmetic."""

    static_traversals: int = 0
    actor_scans: int = 0
    dynamic_scans: int = 0


@dataclass(frozen=True)
class Run:
    attenuation: float
    work: Work

    @property
    def attenuation_bits(self) -> int:
        return float_bits(self.attenuation)


def make_fact(
    ratio: float,
    *,
    projection: str,
    normal: tuple[float, float, float],
    owner_kind: int,
    face_index: int,
    static_hit: bool,
    actor_density: float,
    dynamic_density: float,
) -> ReceiverFact:
    """Build a fact whose ambient/lit divide reproduces *ratio* bit-for-bit."""

    return ReceiverFact(
        projection=projection,
        normal=tuple(f32(component) for component in normal),
        owner_kind=owner_kind,
        face_index=face_index,
        ambient_value=f32(ratio),
        lit_value=ONE,
        static_hit=static_hit,
        actor_density=f32(actor_density),
        dynamic_density=f32(dynamic_density),
    )


def fully_shadowed_ratio(fact: ReceiverFact) -> float:
    """Reproduce the Product divide, lower floor, and clamp."""

    return clamp_f32(
        fdiv(fact.ambient_value, fmax(fact.lit_value, f32(1.0e-4))),
        ZERO,
        ONE,
    )


def blob_attenuation(fact: ReceiverFact) -> float:
    """Reproduce actor/dynamic max-composition and its exact paint law."""

    blob_density = fmax(fact.actor_density, fact.dynamic_density)
    return fmax(
        mix_f32(
            ONE,
            BLOB_LOWER_BOUND,
            clamp_f32(blob_density, ZERO, ONE),
        ),
        ATTENUATION_FLOOR,
    )


def static_attenuation(fact: ReceiverFact, ratio: float) -> float:
    """Reproduce binary retained-static hit/miss attenuation."""

    static_density = ONE if fact.static_hit else ZERO
    return mix_f32(
        ONE,
        ratio,
        clamp_f32(static_density, ZERO, ONE),
    )


def eager_reference(fact: ReceiverFact) -> Run:
    """Evaluate every caster family in the pre-short-circuit order."""

    ratio = fully_shadowed_ratio(fact)
    blob = blob_attenuation(fact)
    static = static_attenuation(fact, ratio)
    return Run(
        fmin(blob, static),
        Work(static_traversals=1, actor_scans=1, dynamic_scans=1),
    )


def bound_dominated_shipping(fact: ReceiverFact) -> Run:
    """Model the shipping low/static-first and high/blob-first traversal law."""

    ratio = fully_shadowed_ratio(fact)
    static_traversals = 0
    actor_scans = 0
    dynamic_scans = 0
    static_density = ZERO

    if ratio <= BLOB_LOWER_BOUND:
        static_traversals += 1
        static_density = ONE if fact.static_hit else ZERO
        if static_density > ZERO:
            return Run(
                ratio,
                Work(static_traversals, actor_scans, dynamic_scans),
            )

    actor_scans += 1
    dynamic_scans += 1
    blob = blob_attenuation(fact)

    if ratio > BLOB_LOWER_BOUND and blob <= ratio:
        return Run(
            blob,
            Work(static_traversals, actor_scans, dynamic_scans),
        )

    if ratio > BLOB_LOWER_BOUND and ratio != ONE:
        static_traversals += 1
        static_density = ONE if fact.static_hit else ZERO

    static = mix_f32(
        ONE,
        ratio,
        clamp_f32(static_density, ZERO, ONE),
    )
    return Run(
        fmin(blob, static),
        Work(static_traversals, actor_scans, dynamic_scans),
    )


def expected_work(fact: ReceiverFact) -> Work:
    """Derive the required traversal counts independently from result math."""

    ratio = fully_shadowed_ratio(fact)
    if ratio <= BLOB_LOWER_BOUND:
        if fact.static_hit:
            return Work(static_traversals=1)
        return Work(static_traversals=1, actor_scans=1, dynamic_scans=1)

    blob = blob_attenuation(fact)
    if blob <= ratio:
        return Work(actor_scans=1, dynamic_scans=1)
    if ratio != ONE:
        return Work(static_traversals=1, actor_scans=1, dynamic_scans=1)
    return Work(actor_scans=1, dynamic_scans=1)


def boundary_ratios(
    density_pairs: tuple[tuple[float, float], ...],
) -> tuple[float, ...]:
    """Include each branch edge plus every blob-equality point in binary32."""

    values = {
        float_bits(ZERO),
        float_bits(next_up_f32(ZERO)),
        float_bits(ATTENUATION_FLOOR),
        float_bits(f32(0.17)),
        float_bits(next_down_f32(BLOB_LOWER_BOUND)),
        float_bits(BLOB_LOWER_BOUND),
        float_bits(next_up_f32(BLOB_LOWER_BOUND)),
        float_bits(f32(0.5)),
        float_bits(f32(0.73)),
        float_bits(next_down_f32(ONE)),
        float_bits(ONE),
    }
    for actor_density, dynamic_density in density_pairs:
        probe = make_fact(
            ONE,
            projection=PERSPECTIVE,
            normal=(0.0, 1.0, 0.0),
            owner_kind=1,
            face_index=-1,
            static_hit=False,
            actor_density=actor_density,
            dynamic_density=dynamic_density,
        )
        blob = blob_attenuation(probe)
        values.add(float_bits(next_down_f32(blob)))
        values.add(float_bits(blob))
        if blob < ONE:
            values.add(float_bits(next_up_f32(blob)))
    return tuple(bits_float(bits) for bits in sorted(values))


def run_binary32_matrix() -> int:
    """Check both projections, all boundaries, exact bits, and exact work."""

    density_pairs = (
        (ZERO, ZERO),
        (f32(0.13), ZERO),
        (ZERO, f32(0.38)),
        (f32(0.5), f32(0.25)),
        (f32(0.87), ONE),
        (ONE, ONE),
    )
    receiver_facts = (
        ((0.0, 1.0, 0.0), 1, -1),
        ((0.31, 0.91, -0.27), 3, 17),
        ((-0.76, 0.18, 0.62), 4, -1),
        ((0.0, -1.0, 0.0), 2, -1),
    )
    ratios = boundary_ratios(density_pairs)
    paired_results: dict[
        tuple[
            tuple[float, float, float],
            int,
            int,
            bool,
            int,
            int,
            int,
        ],
        tuple[int, Work],
    ] = {}
    cases = 0

    for projection in PROJECTIONS:
        for normal, owner_kind, face_index in receiver_facts:
            for static_hit in (False, True):
                for ratio in ratios:
                    for actor_density, dynamic_density in density_pairs:
                        fact = make_fact(
                            ratio,
                            projection=projection,
                            normal=normal,
                            owner_kind=owner_kind,
                            face_index=face_index,
                            static_hit=static_hit,
                            actor_density=actor_density,
                            dynamic_density=dynamic_density,
                        )
                        eager = eager_reference(fact)
                        selected = bound_dominated_shipping(fact)
                        expected = expected_work(fact)
                        context = (
                            projection,
                            normal,
                            owner_kind,
                            face_index,
                            static_hit,
                            float_bits(ratio),
                            float_bits(actor_density),
                            float_bits(dynamic_density),
                        )
                        assert selected.attenuation_bits == eager.attenuation_bits, (
                            "attenuation bits changed",
                            context,
                            eager.attenuation_bits,
                            selected.attenuation_bits,
                        )
                        assert selected.work == expected, (
                            "traversal count changed",
                            context,
                            expected,
                            selected.work,
                        )
                        pair_key = context[1:]
                        paired = paired_results.get(pair_key)
                        result = (selected.attenuation_bits, selected.work)
                        if paired is None:
                            paired_results[pair_key] = result
                        else:
                            assert paired == result, (
                                "projection family changed shadow law",
                                context,
                                paired,
                                result,
                            )
                        cases += 1
    return cases


def extract_glsl_function(source: str, name: str) -> str:
    """Extract one complete GLSL function definition by balanced braces."""

    match = re.search(
        (
            rf"(?m)^[A-Za-z_][A-Za-z0-9_<>, \t]*\b{re.escape(name)}"
            r"\s*\([^;{{}}]*\)\s*\{"
        ),
        source,
    )
    if match is None:
        raise ValueError(f"function definition missing: {name}")
    brace = source.find("{", match.start())
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[match.start() : index + 1]
    raise ValueError(f"function closing brace missing: {name}")


def compact(value: str) -> str:
    """Strip comments and whitespace while preserving executable token order."""

    value = re.sub(r"/\*.*?\*/", "", value, flags=re.S)
    value = re.sub(r"//[^\n]*", "", value)
    return re.sub(r"\s+", "", value)


def source_contract_errors(source: str) -> tuple[str, ...]:
    """Return every executable law violation in the shipping GLSL."""

    errors: list[str] = []
    try:
        cast = compact(
            extract_glsl_function(
                source,
                "q3_probe_payload_cast_shadow_attenuation",
            )
        )
    except ValueError as error:
        return (str(error),)

    static_call = "q3_probe_payload_static_shadow_density("
    actor_call = "actor_row_shadow_density_at_world("
    dynamic_call = "dynamic_mover_shadow_density_at_world("
    if cast.count(static_call) != 2:
        errors.append(
            "cast-shadow must contain exactly two branch-dominated static calls"
        )
    if cast.count(actor_call) != 1:
        errors.append("cast-shadow must scan actor density exactly once")
    if cast.count(dynamic_call) != 1:
        errors.append("cast-shadow must scan dynamic density exactly once")

    low_guard = cast.find("if(fully_shadowed_ratio<=0.38){")
    low_static = cast.find(static_call, low_guard)
    low_hit = cast.find("if(static_density>0.0){", low_static)
    low_return = cast.find("returnfully_shadowed_ratio;", low_hit)
    actor_scan = cast.find(actor_call)
    dynamic_scan = cast.find(dynamic_call)
    blob_density = cast.find(
        "precisefloatblob_density=max("
        "actor_row_shadow_density_at_world(receiver_world),"
        "dynamic_mover_shadow_density_at_world(receiver_world));"
    )
    blob_attenuation = cast.find(
        "precisefloatblob_attenuation=max("
        "mix(1.0,0.38,clamp(blob_density,0.0,1.0)),0.08);"
    )
    high_guard = cast.find(
        "if(fully_shadowed_ratio>0.38"
        "&&blob_attenuation<=fully_shadowed_ratio){"
    )
    high_return = cast.find("returnblob_attenuation;", high_guard)
    required_static_guard = cast.find(
        "if(fully_shadowed_ratio>0.38"
        "&&fully_shadowed_ratio!=1.0){"
    )
    required_static_call = cast.find(static_call, low_static + 1)
    final_attenuation = cast.find(
        "precisefloatstatic_attenuation="
        "q3_probe_payload_static_shadow_light_attenuation("
        "static_density,fully_shadowed_ratio);"
    )
    final_min = cast.find(
        "returnmin(blob_attenuation,static_attenuation);"
    )

    positions = (
        low_guard,
        low_static,
        low_hit,
        low_return,
        blob_density,
        actor_scan,
        dynamic_scan,
        blob_attenuation,
        high_guard,
        high_return,
        required_static_guard,
        required_static_call,
        final_attenuation,
        final_min,
    )
    if any(position < 0 for position in positions):
        errors.append("cast-shadow exact bound expression set is incomplete")
    elif tuple(sorted(positions)) != positions:
        errors.append("cast-shadow low/static and high/blob ordering drifted")

    projection_tokens = (
        "projection",
        "view_identity",
        "VSB_PROJECTION_",
        "is_orthogonal",
        "is_perspective",
    )
    if any(token in cast for token in projection_tokens):
        errors.append("cast-shadow resurrected a projection execution fence")
    return tuple(errors)


def replace_once(source: str, old: str, new: str, name: str) -> str:
    """Apply one reviewed source mutation and reject anchor drift."""

    count = source.count(old)
    if count != 1:
        raise AssertionError(
            f"{name}: mutation anchor count={count}, expected 1: {old!r}"
        )
    return source.replace(old, new, 1)


def braced_statement(source: str, marker: str, name: str) -> str:
    """Return the complete braced statement beginning at one unique marker."""

    if source.count(marker) != 1:
        raise AssertionError(
            f"{name}: braced marker count={source.count(marker)}: {marker!r}"
        )
    start = source.index(marker)
    brace = source.find("{", start)
    if brace < 0:
        raise AssertionError(f"{name}: opening brace missing")
    depth = 0
    for cursor in range(brace, len(source)):
        if source[cursor] == "{":
            depth += 1
        elif source[cursor] == "}":
            depth -= 1
            if depth == 0:
                return source[start : cursor + 1]
    raise AssertionError(f"{name}: closing brace missing")


def damaging_mutants(source: str) -> tuple[tuple[str, str], ...]:
    """Build ten compile-valid regressions matching the reviewed defect list."""

    cast = extract_glsl_function(
        source,
        "q3_probe_payload_cast_shadow_attenuation",
    )
    low_marker = "\tif (fully_shadowed_ratio <= 0.38) {"
    low_block = braced_statement(
        cast,
        low_marker,
        "flattened_low_ratio_ordering",
    )
    static_call = (
        "\tstatic_density = q3_probe_payload_static_shadow_density(\n"
        "\t\treceiver_world, receiver_normal, receiver_owner_kind,\n"
        "\t\treceiver_face_idx, flags);"
    )
    blob_statement = (
        "\tprecise float blob_attenuation = max(\n"
        "\t\tmix(1.0, 0.38, clamp(blob_density, 0.0, 1.0)), 0.08);"
    )

    unconditional = replace_once(
        source,
        low_marker,
        f"{static_call}\n{low_marker}",
        "unconditional_static_traversal_resurrection",
    )
    strict_low = replace_once(
        source,
        low_marker,
        "\tif (fully_shadowed_ratio < 0.38) {",
        "low_boundary_less_equal_changed_to_less",
    )
    compare_density = replace_once(
        source,
        "\t\t\t&& blob_attenuation <= fully_shadowed_ratio) {",
        "\t\t\t&& blob_density <= fully_shadowed_ratio) {",
        "high_dominance_compares_blob_density",
    )
    high_in_low = replace_once(
        source,
        (
            "\tif (fully_shadowed_ratio > 0.38\n"
            "\t\t\t&& blob_attenuation <= fully_shadowed_ratio) {"
        ),
        "\tif (blob_attenuation <= fully_shadowed_ratio) {",
        "high_ratio_dominance_runs_in_low_branch",
    )
    wrong_high_return = replace_once(
        source,
        "\treturn min(blob_attenuation, static_attenuation);",
        "\treturn blob_attenuation;",
        "wrong_return_when_blob_brighter_than_ratio",
    )
    missing_static = replace_once(
        source,
        "\t\t\t&& fully_shadowed_ratio != 1.0) {",
        "\t\t\t&& false) {",
        "required_high_static_traversal_removed",
    )
    max_changed = replace_once(
        source,
        "\tprecise float blob_density = max(",
        "\tprecise float blob_density = min(",
        "actor_dynamic_max_changed",
    )
    projection_fence = replace_once(
        source,
        "\tprecise float fully_shadowed_ratio = clamp(",
        (
            "\tif (product_vsb_frame.view_identity.z "
            "== VSB_PROJECTION_ORTHOGRAPHIC) return 1.0;\n"
            "\tprecise float fully_shadowed_ratio = clamp("
        ),
        "projection_fence_resurrected",
    )
    widened_boundary = replace_once(
        source,
        low_marker,
        (
            "\tif (fully_shadowed_ratio <= "
            "uintBitsToFloat(floatBitsToUint(0.38) + 1u)) {"
        ),
        "low_boundary_widened_one_ulp",
    )

    flattened_cast = replace_once(
        cast,
        low_block,
        "",
        "flattened_low_ratio_ordering",
    )
    flattened_cast = replace_once(
        flattened_cast,
        blob_statement,
        f"{blob_statement}\n{low_block}",
        "flattened_low_ratio_ordering",
    )
    flattened = replace_once(
        source,
        cast,
        flattened_cast,
        "flattened_low_ratio_ordering",
    )
    return (
        ("unconditional_static_traversal_resurrection", unconditional),
        ("low_boundary_less_equal_changed_to_less", strict_low),
        ("high_dominance_compares_blob_density", compare_density),
        ("high_ratio_dominance_runs_in_low_branch", high_in_low),
        ("wrong_return_when_blob_brighter_than_ratio", wrong_high_return),
        ("required_high_static_traversal_removed", missing_static),
        ("actor_dynamic_max_changed", max_changed),
        ("projection_fence_resurrected", projection_fence),
        ("low_boundary_widened_one_ulp", widened_boundary),
        ("flattened_low_ratio_ordering", flattened),
    )


def compile_product(source: str, label: str) -> tuple[bool, str]:
    """Compile one full Product variant through the Vulkan 1.1 GLSL gate."""

    compiler = shutil.which("glslangValidator")
    if compiler is None:
        return False, "glslangValidator is required"
    with tempfile.TemporaryDirectory(
        prefix=f"fl4431-shadow-bound-{label}-"
    ) as directory:
        source_path = Path(directory) / "product.comp"
        output_path = Path(directory) / "product.spv"
        source_path.write_text(
            source.replace("#[compute]\n", "", 1),
            encoding="utf-8",
        )
        try:
            result = subprocess.run(
                [
                    compiler,
                    "--target-env",
                    "vulkan1.1",
                    "-S",
                    "comp",
                    "-o",
                    str(output_path),
                    str(source_path),
                ],
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return False, f"{label}: glslangValidator timed out"
        detail = (result.stdout + result.stderr).strip()
        return result.returncode == 0, detail


def run_contract() -> tuple[int, int]:
    """Run matrix, source law, shipping compile, and destructive mutations."""

    source = _expand_includes(PRODUCT_PATH.read_text(encoding="utf-8"))
    errors = source_contract_errors(source)
    assert errors == (), "\n".join(errors)

    matrix_cases = run_binary32_matrix()
    compiled, detail = compile_product(source, "shipping")
    assert compiled, f"shipping Product did not compile:\n{detail}"

    mutants = damaging_mutants(source)
    assert len(mutants) == 10
    escaped: list[str] = []
    for name, mutant in mutants:
        assert mutant != source, f"{name}: mutation did not alter source"
        compiled, detail = compile_product(mutant, name)
        assert compiled, (
            f"{name}: destructive mutant was not compile-valid\n{detail}"
        )
        if source_contract_errors(mutant) == ():
            escaped.append(name)
    assert escaped == [], f"compile-valid destructive mutants escaped: {escaped}"
    return matrix_cases, len(mutants)


def test_shadow_bound_short_circuit_contract() -> None:
    """Pytest entry point for the complete direct-execution contract."""

    run_contract()


def main() -> int:
    try:
        matrix_cases, mutant_count = run_contract()
    except (
        AssertionError,
        OSError,
        subprocess.SubprocessError,
        ValueError,
    ) as error:
        print(f"FL4431_SHADOW_BOUND_SHORT_CIRCUIT_CONTRACT_FAIL {error}")
        return 1
    print(
        "FL4431_SHADOW_BOUND_SHORT_CIRCUIT_CONTRACT_PASS "
        f"projections={len(PROJECTIONS)} matrix_cases={matrix_cases} "
        f"compile_valid_mutants={mutant_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

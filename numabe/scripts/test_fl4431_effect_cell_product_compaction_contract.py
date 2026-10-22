#!/usr/bin/env python3
"""FL-4431 EffectCellProduct source and generated-SPIR-V compaction contract.

The retained-scene Product pass layers glyph-light and lighthouse-beam paint
through one short-lived EffectCellProduct value. Actor dash and attack paint
belong to the actor-row/compositor path and must not return as Product effect
producers. The surviving local-light product also carries stable source and
phase identity for the final secondary-glyph receipt.

This contract checks both representations:

* GLSL must declare exactly the nine paint/identity fields and each producer
  may write only its reviewed subset.
* Generated SPIR-V must expose the same nine named struct members and the
  reviewed member-type sequence.

Every destructive mutant is compiled before the contract rejects it.  Syntax
damage therefore cannot masquerade as protection. Six mutants resurrect the
removed field families, three restore real dead producer stores, and two
delete the stable local-light identity publication.
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
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
PRODUCT = ROOT / "godot_project/render/product/cell_owner_product.glsl"

EXPECTED_FIELDS = (
    ("bool", "present"),
    ("uint", "glyph"),
    ("uint", "source_key"),
    ("uint", "phase_key"),
    ("uint", "blend"),
    ("float", "coverage"),
    ("float", "alpha"),
    ("vec3", "fg"),
    ("float", "intensity"),
)
EXPECTED_SPIRV_TYPES = (
    "%bool",
    "%uint",
    "%uint",
    "%uint",
    "%uint",
    "%float",
    "%float",
    "%v3float",
    "%float",
)
REMOVED_FIELDS = (
    ("uint", "kind", "OWNER_NONE"),
    ("uint", "source_id", "0u"),
    ("uint", "reject_bits", "0u"),
    ("float", "depth", "1.0e20"),
    ("vec3", "world", "vec3(0.0)"),
    ("float", "aux", "0.0"),
)


@dataclass(frozen=True)
class Mutant:
    name: str
    source: str
    spirv_marker: str


def _standalone_shader(source: str) -> str:
    """Remove Godot's resource marker that is not part of GLSL."""

    return source.replace("#[compute]\n", "", 1)


def _extract_braced_block(source: str, start: int) -> str:
    """Return one balanced brace block beginning at or after *start*."""

    opening = source.find("{", start)
    if opening < 0:
        raise ValueError(f"opening brace missing after byte {start}")
    depth = 0
    for cursor in range(opening, len(source)):
        char = source[cursor]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[opening : cursor + 1]
    raise ValueError(f"closing brace missing after byte {start}")


def _function(source: str, name: str) -> str:
    """Extract one named function body without matching its call sites."""

    match = re.search(
        (
            rf"(?m)^[A-Za-z_][A-Za-z0-9_<>, \t]*\b{re.escape(name)}"
            r"\s*\([^;{}]*\)\s*\{"
        ),
        source,
    )
    if match is None:
        raise ValueError(f"function definition missing: {name}")
    return _extract_braced_block(source, match.start())


def _struct_fields(source: str) -> tuple[tuple[str, str], ...]:
    """Parse the concrete EffectCellProduct declaration in source order."""

    match = re.search(
        r"\bstruct\s+EffectCellProduct\s*\{(?P<body>.*?)\};",
        source,
        flags=re.DOTALL,
    )
    if match is None:
        raise ValueError("EffectCellProduct declaration missing")
    fields: list[tuple[str, str]] = []
    for declaration in match.group("body").split(";"):
        declaration = re.sub(r"//.*", "", declaration).strip()
        if not declaration:
            continue
        parts = declaration.split()
        if len(parts) != 2:
            raise ValueError(
                f"unexpected EffectCellProduct member declaration: {declaration!r}"
            )
        fields.append((parts[0], parts[1]))
    return tuple(fields)


def _assigned_members(body: str, receiver: str) -> tuple[str, ...]:
    """Return unique members assigned through one producer-local receiver."""

    members = re.findall(
        rf"\b{re.escape(receiver)}\.([A-Za-z_][A-Za-z0-9_]*)\s*=",
        body,
    )
    return tuple(dict.fromkeys(members))


def _spirv_effect_members(assembly: str) -> tuple[str, ...]:
    """Read named EffectCellProduct members from debug-preserving SPIR-V."""

    rows = re.findall(
        r'OpMemberName\s+%EffectCellProduct\s+(\d+)\s+"([^"]+)"',
        assembly,
    )
    indexed = sorted((int(index), name) for index, name in rows)
    if [index for index, _name in indexed] != list(range(len(indexed))):
        raise AssertionError(
            f"SPIR-V EffectCellProduct member indices drifted: {indexed!r}"
        )
    return tuple(name for _index, name in indexed)


def _spirv_effect_types(assembly: str) -> tuple[str, ...]:
    """Read the concrete OpTypeStruct operand sequence."""

    match = re.search(
        r"(?m)^%EffectCellProduct\s*=\s*OpTypeStruct\s+(.+)$",
        assembly,
    )
    if match is None:
        raise ValueError("SPIR-V EffectCellProduct OpTypeStruct missing")
    return tuple(match.group(1).split())


def _compile_and_disassemble(source: str, label: str) -> tuple[bool, str, str]:
    """Compile full Product GLSL and return its named SPIR-V assembly."""

    compiler = shutil.which("glslangValidator")
    disassembler = shutil.which("spirv-dis")
    if compiler is None:
        return False, "", "glslangValidator is required"
    if disassembler is None:
        return False, "", "spirv-dis is required"
    with tempfile.TemporaryDirectory(
        prefix=f"fl4431-effect-{label}-"
    ) as directory:
        temp_dir = Path(directory)
        shader_path = temp_dir / "product.comp"
        spirv_path = temp_dir / "product.spv"
        assembly_path = temp_dir / "product.spvasm"
        shader_path.write_text(_standalone_shader(source), encoding="utf-8")
        try:
            compile_result = subprocess.run(
                [
                    compiler,
                    "--target-env",
                    "vulkan1.1",
                    "-S",
                    "comp",
                    "-g",
                    "-o",
                    str(spirv_path),
                    str(shader_path),
                ],
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return False, "", f"{label}: glslangValidator timed out"
        if compile_result.returncode != 0:
            diagnostic = (compile_result.stdout + compile_result.stderr).strip()
            return False, "", f"{label}: glslangValidator rejected GLSL:\n{diagnostic}"
        disassemble_result = subprocess.run(
            [disassembler, str(spirv_path), "-o", str(assembly_path)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if disassemble_result.returncode != 0:
            diagnostic = (
                disassemble_result.stdout + disassemble_result.stderr
            ).strip()
            return False, "", f"{label}: spirv-dis failed:\n{diagnostic}"
        return True, assembly_path.read_text(encoding="utf-8"), ""


def contract_errors(source: str, assembly: str) -> tuple[str, ...]:
    """Return every compaction violation without hiding later failures."""

    errors: list[str] = []
    try:
        fields = _struct_fields(source)
    except ValueError as error:
        return (str(error),)
    if fields != EXPECTED_FIELDS:
        errors.append(
            "EffectCellProduct GLSL members drifted: "
            f"expected={EXPECTED_FIELDS!r} actual={fields!r}"
        )

    expected_member_names = tuple(name for _type_name, name in EXPECTED_FIELDS)
    try:
        spirv_members = _spirv_effect_members(assembly)
        spirv_types = _spirv_effect_types(assembly)
    except ValueError as error:
        errors.append(str(error))
    else:
        if spirv_members != expected_member_names:
            errors.append(
                "EffectCellProduct SPIR-V members drifted: "
                f"expected={expected_member_names!r} actual={spirv_members!r}"
            )
        if spirv_types != EXPECTED_SPIRV_TYPES:
            errors.append(
                "EffectCellProduct SPIR-V member types drifted: "
                f"expected={EXPECTED_SPIRV_TYPES!r} actual={spirv_types!r}"
            )

    producer_contracts = (
        ("compute_local_light_glyph_cell_product", "product"),
    )
    expected_assignments = expected_member_names
    for function_name, receiver in producer_contracts:
        try:
            body = _function(source, function_name)
        except ValueError as error:
            errors.append(str(error))
            continue
        assigned = _assigned_members(body, receiver)
        if assigned != expected_assignments:
            errors.append(
                f"{function_name} writes {assigned!r}, "
                f"expected {expected_assignments!r}"
            )
        if function_name == "compute_local_light_glyph_cell_product":
            for dead_name in ("best_src", "best_world"):
                if re.search(rf"\b{dead_name}\b", body):
                    errors.append(
                        f"glyph-light producer resurrected dead {dead_name}"
                    )
            for marker in (
                "best_source_key = local_light_glyph_source_key(i);",
                "best_phase_key = local_light_glyph_phase_key(i);",
                "product.source_key = best_source_key;",
                "product.phase_key = best_phase_key;",
            ):
                if marker not in body:
                    errors.append(
                        f"glyph-light stable identity publication missing: {marker}"
                    )

    for retired_name in (
        "actor_dash_trail_cell_product",
        "actor_attack_swoosh_cell_product",
    ):
        try:
            _function(source, retired_name)
        except ValueError:
            pass
        else:
            errors.append(
                f"retired Product actor effect owner returned: {retired_name}"
            )

    try:
        collector = _function(
            source, "vsb_product_collect_local_light_slot_facts"
        )
    except ValueError as error:
        errors.append(str(error))
    else:
        beam_assignments = _assigned_members(collector, "facts.beam")
        expected_beam_assignments = tuple(
            name for name in expected_assignments
            if name not in ("source_key", "phase_key")
        )
        if beam_assignments != expected_beam_assignments:
            errors.append(
                "lighthouse beam writes "
                f"{beam_assignments!r}, expected {expected_beam_assignments!r}"
            )
        for dead_name in ("best_beam_source", "best_beam_world"):
            if re.search(rf"\b{dead_name}\b", collector):
                errors.append(f"beam producer resurrected dead {dead_name}")

    try:
        finalizer = _function(source, "vsb_product_finalize_record")
    except ValueError as error:
        errors.append(str(error))
    else:
        if "EffectCellProduct attack_copy" in finalizer:
            errors.append("finalizer resurrected the eliminated attack copy")
        if "actor.attack_product" in finalizer:
            errors.append(
                "finalizer resurrected the retired Product actor effect owner"
            )
        for marker in (
            "vsb_product_apply_effect(glyph_light, record);",
            "glyph_light.source_key,",
            "glyph_light.phase_key,",
        ):
            if marker not in finalizer:
                errors.append(
                    f"finalizer lost local-light identity handoff: {marker}"
                )

    return tuple(errors)


def _replace_once(source: str, old: str, new: str, name: str) -> str:
    """Apply one mutation only when its reviewed anchor is unique."""

    count = source.count(old)
    if count != 1:
        raise AssertionError(
            f"{name}: mutation anchor count={count}, expected 1: {old!r}"
        )
    return source.replace(old, new, 1)


def _add_removed_field(
    source: str,
    *,
    field_type: str,
    field_name: str,
    initializer: str,
    function_name: str,
    store_anchor: str,
    store_line: str,
    mutant_name: str,
) -> str:
    """Restore one old member plus a compiling producer write."""

    struct_anchor = "\tfloat intensity;\n};"
    struct_match = re.search(
        r"\bstruct\s+EffectCellProduct\s*\{.*?\};",
        source,
        flags=re.DOTALL,
    )
    if struct_match is None:
        raise AssertionError(f"{mutant_name}: EffectCellProduct missing")
    struct_source = struct_match.group(0)
    mutated_struct = _replace_once(
        struct_source,
        struct_anchor,
        f"\tfloat intensity;\n\t{field_type} {field_name};\n}};",
        mutant_name,
    )
    mutated = (
        source[: struct_match.start()]
        + mutated_struct
        + source[struct_match.end() :]
    )
    function_body = _function(mutated, function_name)
    mutated_body = _replace_once(
        function_body,
        store_anchor,
        f"{store_anchor}\n{store_line.format(value=initializer)}",
        mutant_name,
    )
    return _replace_once(
        mutated,
        function_body,
        mutated_body,
        mutant_name,
    )


def damaging_mutants(source: str) -> tuple[Mutant, ...]:
    """Build compile-valid regressions representing the deleted work."""

    mutants: list[Mutant] = []
    empty_anchor = "\tproduct.intensity = 0.0;"
    for field_type, field_name, initializer in REMOVED_FIELDS:
        name = f"field_{field_name}_resurrected"
        mutated = _add_removed_field(
            source,
            field_type=field_type,
            field_name=field_name,
            initializer=initializer,
            function_name="empty_effect_cell_product",
            store_anchor=empty_anchor,
            store_line=f"\tproduct.{field_name} = {{value}};",
            mutant_name=name,
        )
        mutants.append(Mutant(name, mutated, f'"{field_name}"'))

    glyph = _add_removed_field(
        source,
        field_type="uint",
        field_name="source_id",
        initializer="1u",
        function_name="compute_local_light_glyph_cell_product",
        store_anchor=(
            "\tproduct.fg = clamp(best_color, vec3(0.0), vec3(1.0));"
        ),
        store_line="\tproduct.source_id = {value};",
        mutant_name="glyph_dead_source_store_resurrected",
    )
    mutants.append(
        Mutant("glyph_dead_source_store_resurrected", glyph, '"source_id"')
    )

    local_light = _add_removed_field(
        source,
        field_type="uint",
        field_name="reject_bits",
        initializer="uint(final_kind)",
        function_name="compute_local_light_glyph_cell_product",
        store_anchor="\tproduct.intensity = 1.0;",
        store_line="\tproduct.reject_bits = {value};",
        mutant_name="glyph_dead_local_light_provenance_resurrected",
    )
    mutants.append(
        Mutant(
            "glyph_dead_local_light_provenance_resurrected",
            local_light,
            '"reject_bits"',
        )
    )

    beam = _add_removed_field(
        source,
        field_type="float",
        field_name="aux",
        initializer="best_beam_coverage",
        function_name="vsb_product_collect_local_light_slot_facts",
        store_anchor=(
            "\t\tfacts.beam.intensity =\n"
            "\t\t\tclamp(best_beam_contribution * 0.48, 0.0, 1.0);"
        ),
        store_line="\t\tfacts.beam.aux = {value};",
        mutant_name="beam_dead_aux_store_resurrected",
    )
    mutants.append(
        Mutant("beam_dead_aux_store_resurrected", beam, '"aux"')
    )

    source_key_drop = _replace_once(
        source,
        "\tproduct.source_key = best_source_key;",
        "\tproduct.source_key = 0u;",
        "local_light_source_key_dropped",
    )
    mutants.append(
        Mutant(
            "local_light_source_key_dropped",
            source_key_drop,
            '"source_key"',
        )
    )
    phase_key_drop = _replace_once(
        source,
        "\tproduct.phase_key = best_phase_key;",
        "\tproduct.phase_key = 0u;",
        "local_light_phase_key_dropped",
    )
    mutants.append(
        Mutant(
            "local_light_phase_key_dropped",
            phase_key_drop,
            '"phase_key"',
        )
    )
    return tuple(mutants)


def run_contract() -> tuple[int, int]:
    """Run green source/SPIR-V checks plus every red-first mutant."""

    source = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    compiled, assembly, diagnostic = _compile_and_disassemble(
        source, "shipping"
    )
    assert compiled, diagnostic
    errors = contract_errors(source, assembly)
    assert errors == (), "\n".join(errors)

    mutants = damaging_mutants(source)
    assert len(mutants) == 11
    escaped: list[str] = []
    for mutant in mutants:
        assert mutant.source != source, f"{mutant.name}: source did not change"
        compiled, mutant_assembly, diagnostic = _compile_and_disassemble(
            mutant.source, mutant.name
        )
        assert compiled, (
            f"{mutant.name}: destructive mutant was not compile-valid\n"
            f"{diagnostic}"
        )
        marker_present = (
            mutant.spirv_marker.strip('"')
            in _spirv_effect_members(mutant_assembly)
        )
        assert marker_present, (
            f"{mutant.name}: generated SPIR-V lacks "
            f"the concrete {mutant.spirv_marker} symbol"
        )
        if contract_errors(mutant.source, mutant_assembly) == ():
            escaped.append(mutant.name)
    assert escaped == [], f"compile-valid destructive mutants escaped: {escaped}"
    return len(EXPECTED_FIELDS), len(mutants)


def test_effect_cell_product_compaction_contract() -> None:
    """Pytest entry point for the same complete contract as direct execution."""

    run_contract()


def main() -> int:
    try:
        member_count, mutant_count = run_contract()
    except (AssertionError, OSError, subprocess.SubprocessError, ValueError) as error:
        print(f"FL4431_EFFECT_CELL_PRODUCT_COMPACTION_CONTRACT_FAIL {error}")
        return 1
    print(
        "FL4431_EFFECT_CELL_PRODUCT_COMPACTION_CONTRACT_PASS "
        f"glsl_members={member_count} spirv_members={member_count} "
        f"compile_valid_mutants={mutant_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

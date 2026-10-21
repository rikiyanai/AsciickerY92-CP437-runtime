#!/usr/bin/env python3
"""Static and compiler contract for the FL-4231 F13 capture-only seam.

This test deliberately treats the current shader/host pair as the candidate
and the authorized Product blob as the frozen baseline. It proves that the
diagnostic lane is opt-in,
that its new records remain a private observation surface, and that the
ordinary Product output dataflow is unchanged after constant 17 is frozen off.
"""

from __future__ import annotations
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


from pathlib import Path
import re
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
PRODUCT_PATH = ROOT / "godot_project/render/product/cell_owner_product.glsl"
HOST_PATH = ROOT / "godot_project/render/api/ASCIICompositorEffect.gd"
HOST_GIT_PATH = "godot_project/render/api/ASCIICompositorEffect.gd"
# Re-read at the current checkout: this is the committed Product blob against
# which the capture-only lane protects ordinary b55 output dataflow.
BASELINE_PRODUCT_BLOB = "2fd798a6e0e535d114d1ea228fae4afef75dd011"


class Checks:
    def __init__(self) -> None:
        self.names: list[str] = []
        self.failures: list[str] = []

    def require(self, name: str, condition: bool, detail: str = "") -> None:
        self.names.append(name)
        if not condition:
            self.failures.append(f"{name}: {detail or 'contract mismatch'}")


def _balanced_body(source: str, function_name: str) -> str:
    """Extract a function body while ignoring braces in comments and strings."""

    name = re.compile(rf"\b{re.escape(function_name)}\s*\(")
    for match in name.finditer(source):
        cursor = match.end() - 1
        depth = 0
        quote = ""
        escaped = False
        while cursor < len(source):
            char = source[cursor]
            if quote:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = ""
                cursor += 1
                continue
            if source.startswith("//", cursor):
                newline = source.find("\n", cursor + 2)
                cursor = len(source) if newline < 0 else newline + 1
                continue
            if source.startswith("/*", cursor):
                end = source.find("*/", cursor + 2)
                cursor = len(source) if end < 0 else end + 2
                continue
            if char in "\"'":
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    break
            cursor += 1
        if depth != 0:
            continue
        cursor += 1
        while cursor < len(source) and (source[cursor].isspace() or source[cursor] == ":"):
            cursor += 1
        if cursor >= len(source) or source[cursor] != "{":
            continue
        opening = cursor
        cursor += 1
        depth = 1
        quote = ""
        escaped = False
        while cursor < len(source):
            char = source[cursor]
            if quote:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = ""
                cursor += 1
                continue
            if source.startswith("//", cursor):
                newline = source.find("\n", cursor + 2)
                cursor = len(source) if newline < 0 else newline + 1
                continue
            if source.startswith("/*", cursor):
                end = source.find("*/", cursor + 2)
                cursor = len(source) if end < 0 else end + 2
                continue
            if char in "\"'":
                quote = char
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return source[opening : cursor + 1]
            cursor += 1
    raise ValueError(f"function body not found: {function_name}")


def _git_baseline() -> str:
    return subprocess.check_output(
        ["git", "cat-file", "blob", BASELINE_PRODUCT_BLOB], cwd=ROOT, text=True
    )


def _standalone(source: str) -> str:
    return source.replace("#[compute]\n", "", 1)


def _q3_members(source: str) -> list[tuple[str, str]]:
    match = re.search(
        r"struct\s+Q3ProbeSubcellDiagnosticRecord\s*\{(?P<body>.*?)\};",
        source,
        flags=re.DOTALL,
    )
    if match is None:
        return []
    members: list[tuple[str, str]] = []
    for declaration in match.group("body").split(";"):
        declaration = re.sub(r"//.*", "", declaration).strip()
        if not declaration:
            continue
        parts = declaration.split()
        if len(parts) >= 2:
            members.append((parts[0], parts[1]))
    return members


def _capture_functions(source: str) -> dict[str, str]:
    names = sorted(set(re.findall(r"\b(product_capture_[A-Za-z0-9_]+)\s*\(", source)))
    return {name: _balanced_body(source, name) for name in names}


def _gd_function(source: str, function_name: str) -> str:
    """Extract one indented GDScript function through the next top-level func."""

    match = re.search(rf"(?m)^func\s+{re.escape(function_name)}\s*\(", source)
    if match is None:
        raise ValueError(f"GDScript function not found: {function_name}")
    end = re.search(r"(?m)^func\s+", source[match.end():])
    stop = len(source) if end is None else match.end() + end.start()
    return source[match.start():stop]


def _assignment_lhs(body: str) -> list[str]:
    clean = re.sub(r"//.*", "", body)
    clean = re.sub(r"/\*.*?\*/", "", clean, flags=re.DOTALL)
    return re.findall(r"(?m)(?:^|[;{}])\s*([A-Za-z_][A-Za-z0-9_.]*)\s*=", clean)


def _guarded_region(source: str, needle: str) -> str:
    index = source.find(needle)
    return source[index:] if index >= 0 else ""


def contract_errors(product: str, host: str, baseline_product: str) -> list[str]:
    errors: list[str] = []

    members = _q3_members(product)
    expected_members = [("uvec4", f"r{i}") for i in range(7)]
    if members != expected_members:
        errors.append(f"b62 schema is {members!r}, expected {expected_members!r}")
    if "layout(set = 0, binding = 62, std430) buffer Q3ProbeSubcellDiagnostics" not in product:
        errors.append("b62 binding 62 declaration missing")
    if "const VSB_PRODUCT_Q3_DIAGNOSTIC_RECORD_BYTES := 112" not in host:
        errors.append("host b62 record size is not 112 bytes")
    if "layout(constant_id = 17) const bool PRODUCT_B62_DIAGNOSTICS_ENABLED = false;" not in product:
        errors.append("constant 17 false default missing")

    expected_r3 = "terrain role, contact shadow, palette branch, pre-edge glyph"
    expected_r4 = "edge admitted, reason bits, floatBits(score), post-edge glyph"
    expected_r5 = "godray lit bits, sampled bits, sample count, floatBits(luminance)"
    expected_r6 = "floatBits(godray accumulated RGB), reserved"
    if f"uvec4 r3; // {expected_r3}" not in product:
        errors.append("r3 schema/comment mismatch")
    if f"uvec4 r4; // {expected_r4}" not in product:
        errors.append("r4 schema/comment mismatch")
    if f"uvec4 r5; // {expected_r5}" not in product:
        errors.append("r5 schema/comment mismatch")
    if f"uvec4 r6; // {expected_r6}" not in product:
        errors.append("r6 schema/comment mismatch")
    if "record_index * VSB_PRODUCT_Q3_DIAGNOSTIC_RECORD_BYTES + 12" not in host:
        errors.append("r0.w marker no longer reads byte 12")
    expected_offsets = ("base + 48", "base + 52", "base + 56", "base + 60", "base + 64", "base + 68", "base + 72", "base + 76", "base + 80", "base + 84", "base + 88", "base + 92", "base + 96", "base + 100", "base + 104")
    if not all(offset in host for offset in expected_offsets):
        errors.append("r3/r4/r5/r6 host offsets are incomplete")
    for key in (
        '"godray_lit_bits"',
        '"godray_sample_mask"',
        '"godray_sample_count"',
        '"godray_accumulated_luminance"',
        '"godray_accumulated_light"',
    ):
        if key not in host:
            errors.append(f"b62 godray decoder field missing: {key}")
    if "product_capture_terrain_role,\n\t\t\tproduct_capture_contact_shadow,\n\t\t\tproduct_capture_palette_branch,\n\t\t\tproduct_capture_edge_pre_glyph" not in product:
        errors.append("r3 publication schema mismatch")
    if "product_capture_edge_admitted,\n\t\t\tproduct_capture_edge_reason_bits,\n\t\t\tfloatBitsToUint(product_capture_edge_score),\n\t\t\tproduct_capture_edge_post_glyph" not in product:
        errors.append("r4 publication schema mismatch")
    if "product_capture_godray_lit_bits,\n\t\t\t\tproduct_capture_godray_sample_mask,\n\t\t\t\tproduct_capture_godray_sample_count" not in product:
        errors.append("r5 publication schema mismatch")
    if "floatBitsToUint(product_capture_godray_accumulated_light.r),\n\t\t\t\tfloatBitsToUint(product_capture_godray_accumulated_light.g),\n\t\t\t\tfloatBitsToUint(product_capture_godray_accumulated_light.b)" not in product:
        errors.append("r6 publication schema mismatch")

    try:
        finalizer = _balanced_body(
            product, "vsb_product_finalize_record"
        )
        presentation = _balanced_body(
            product, "product_daylight_godray_veil"
        )
    except ValueError as error:
        errors.append(f"godray capture owner body missing: {error}")
    else:
        if "bool capture_godray_visibility = PRODUCT_B62_DIAGNOSTICS_ENABLED" not in finalizer:
            errors.append("godray live capture flag is not diagnostics-gated")
        if "(subcell.x & 1) == 0\n\t\t\t&& (subcell.y & 1) == 0" not in finalizer:
            errors.append("godray live capture is not restricted to the upper-left quarter")
        if "product_daylight_godray_veil(\n\t\t\tinput_value.ray, effect_depth, capture_godray_visibility)" not in finalizer:
            errors.append("godray live capture flag is not passed into the presentation helper")
        for pin in (
            "if (capture_visibility) {",
            "product_capture_godray_sample_mask |= 1u << uint(i);",
            "product_capture_godray_sample_count += 1u;",
            "product_capture_godray_lit_bits |= 1u << uint(i);",
            "product_capture_godray_accumulated_light = veil.light;",
        ):
            if pin not in presentation:
                errors.append(f"godray live capture lost {pin}")
        helper_without_comments = "\n".join(
            line.split("//", 1)[0] for line in presentation.splitlines()
        )
        if "record.fg" in helper_without_comments or "record.bg" in helper_without_comments:
            errors.append("godray helper still cannot read or write Product paint")
        if "product_capture_godray_" in helper_without_comments:
            capture_reads = [
                token for token in (
                    "product_capture_godray_lit_bits",
                    "product_capture_godray_sample_mask",
                    "product_capture_godray_sample_count",
                    "product_capture_godray_accumulated_light",
                )
                if f"{token} =" not in helper_without_comments
                and f"{token} |=" not in helper_without_comments
                and f"{token} +=" not in helper_without_comments
            ]
            if capture_reads:
                errors.append(f"presentation helper reads capture outputs: {capture_reads}")

    if "var _fl4431_secondary_effect_evidence_enabled: bool = false" not in host:
        errors.append("secondary evidence flag is not default false")
    if 'OS.get_cmdline_user_args().has(\n\t\t\t"--fl4431-secondary-effect-evidence"' not in host:
        errors.append("secondary evidence command flag is missing")
    ensure = _gd_function(host, "_ensure_vsb_product_q3_read_facts")
    if "if _fl4431_secondary_effect_evidence_enabled:" not in ensure:
        errors.append("b62 allocation is not flag-gated")
    if ensure.find("rd.storage_buffer_create") >= 0 and ensure.find("if _fl4431_secondary_effect_evidence_enabled:") > ensure.find("rd.storage_buffer_create"):
        errors.append("b62 allocation occurs before its flag gate")
    rows = _gd_function(host, "build_final_render_cell_rows")
    if "if _fl4431_secondary_effect_evidence_enabled:" not in rows:
        errors.append("b62 row readback is not flag-gated")
    if "capture_diagnostic_data = rd.buffer_get_data" not in rows:
        errors.append("b62 row readback missing")
    if "diagnostic_index_valid =" not in product or "PRODUCT_B62_DIAGNOSTICS_ENABLED" not in product:
        errors.append("shader per-final-index b62 gate missing")

    for function_name in (
        "q3_probe_payload_apply_world_cell_edge",
        "product_apply_terrain_plane_palette",
    ):
        try:
            if _balanced_body(product, function_name) != _balanced_body(baseline_product, function_name):
                errors.append(f"protected function changed: {function_name}")
        except ValueError as error:
            errors.append(str(error))

    capture_functions = _capture_functions(product)
    if not capture_functions:
        errors.append("capture helper family missing")
    directly_gated = {
        "product_capture_terrain_palette_decision",
        "product_capture_world_cell_edge_decision",
        "product_capture_godray_plane_visibility",
    }
    for name, body in capture_functions.items():
        if name in directly_gated and "if (!PRODUCT_B62_DIAGNOSTICS_ENABLED) return;" not in body:
            errors.append(f"capture helper lacks specialization gate: {name}")
        lhs = _assignment_lhs(body)
        prohibited = [
            item for item in lhs
            if item in {"glyph", "record", "final_record", "fg", "bg"}
            or item.endswith(".r0")
            or item.endswith(".r0.z")
            or item.endswith(".fg")
            or item.endswith(".bg")
            or "admission" in item
        ]
        if prohibited:
            errors.append(f"capture helper writes Product paint/admission state: {name}: {prohibited}")
    if any(
        f"q3_probe_subcell_diagnostics.records[final_index].{row}" not in product
        for row in ("r3", "r4", "r5", "r6")
    ):
        errors.append("b62 r3/r4/r5/r6 stores are missing")
    if "if (diagnostic_index_valid) {\n\t\t\tproduct_capture_reset_terrain_edge_diagnostic();" not in product:
        errors.append("capture reset is not under the per-final-index diagnostic gate")
    if product.count("product_capture_cell_edge_fact(") != 5:
        errors.append("edge-fact helper has an unexpected call/definition count")

    required_row_keys = (
        '"final_product_b55_quarters"',
        '"terrain_edge_b62_quarters"',
        '"joined_surfaces"] = [',
        '"b55_final_product"',
        '"b62_terrain_edge_diagnostic"',
        '"b64_selected_surface"',
        '"b43_final_cell_paint"',
        '"viewport_rgba8"',
    )
    for key in required_row_keys:
        if key not in host:
            errors.append(f"same-request row join missing: {key}")
    if 'capture_product_record["final_index"] = capture_final_index' not in rows or "capture_final_index * VSB_FINAL_PRODUCT_RECORD_BYTES" not in rows:
        errors.append("b55 capture rows are not keyed by final index")
    if "_fl4231_decode_terrain_edge_diagnostic" not in rows or re.search(r"capture_diagnostic_data,\s*capture_final_index", rows) is None:
        errors.append("b62 capture rows are not keyed by final index")
    metadata = _gd_function(host, "build_final_render_dump_metadata")
    for key in ('terrain_edge_capture_receipt["git_head"] = git_head', 'terrain_edge_capture_receipt["dump_frame_index"] = pixel_camera_frame_index', '"fl4231_terrain_edge_capture"'):
        if key not in host:
            errors.append(f"capture metadata identity missing: {key}")
    if "git_head" not in metadata or "dump_frame_index" not in metadata:
        errors.append("metadata builder does not carry frame/git identity")

    return errors


def _compile(source: str, label: str, output: Path) -> tuple[bool, str]:
    compiler = shutil.which("glslangValidator")
    if compiler is None:
        return False, "glslangValidator is required"
    shader = output.with_suffix(".comp")
    shader.write_text(_standalone(source), encoding="utf-8")
    result = subprocess.run(
        [compiler, "--target-env", "vulkan1.1", "-S", "comp", "-g", "-o", str(output), str(shader)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    detail = (result.stdout + result.stderr).strip()
    return result.returncode == 0, detail


def _optimized(source: str, label: str, directory: Path) -> tuple[bool, bytes, str]:
    raw = directory / f"{label}.spv"
    optimized = directory / f"{label}.optimized.spv"
    ok, detail = _compile(source, label, raw)
    if not ok:
        return False, b"", detail
    optimizer = shutil.which("spirv-opt")
    if optimizer is None:
        return False, b"", "spirv-opt is required"
    args = [
        optimizer,
        "--set-spec-const-default-value", "17:false",
        "--freeze-spec-const",
        "--fold-spec-const-op-composite",
        "--eliminate-dead-branches",
        "--eliminate-dead-code-aggressive",
        "--eliminate-dead-const",
        "--eliminate-dead-functions",
        "--eliminate-dead-variables",
        "--preserve-bindings",
        "--strip-debug",
        "--compact-ids",
        "-o", str(optimized), str(raw),
    ]
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=300, check=False)
    detail = (result.stdout + result.stderr).strip()
    if result.returncode != 0:
        return False, b"", f"{label}: spirv-opt failed: {detail}"
    return True, optimized.read_bytes(), detail


def _disassemble(binary: bytes, directory: Path, label: str) -> str:
    source = directory / f"{label}.normalized.spv"
    assembly = directory / f"{label}.normalized.spvasm"
    source.write_bytes(binary)
    result = subprocess.run(
        [shutil.which("spirv-dis") or "spirv-dis", str(source), "-o", str(assembly)],
        cwd=ROOT, capture_output=True, text=True, timeout=120, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stdout + result.stderr).strip())
    return assembly.read_text(encoding="utf-8")


def _b55_projection(assembly: str) -> tuple[str, ...]:
    """Project normalized SPIR-V to binding-55 pointer/store/value dataflow."""

    lines = [line.split(";", 1)[0].strip() for line in assembly.splitlines()]
    parsed: list[tuple[str | None, str, list[str], str]] = []
    for line in lines:
        if not line or line.startswith(";"):
            continue
        tokens = line.split()
        result_id: str | None = None
        if len(tokens) >= 3 and tokens[1] == "=":
            result_id = tokens[0]
            opcode = tokens[2]
            operands = tokens[3:]
        else:
            opcode = tokens[0]
            operands = tokens[1:]
        parsed.append((result_id, opcode, operands, line))

    binding_ids = {
        operands[0]
        for _result, opcode, operands, _line in parsed
        if opcode == "OpDecorate"
        and len(operands) >= 3
        and operands[-2:] == ["Binding", "55"]
    }
    if not binding_ids:
        raise ValueError("binding 55 decoration missing from normalized SPIR-V")

    pointer_ops = {
        "OpAccessChain",
        "OpInBoundsAccessChain",
        "OpPtrAccessChain",
        "OpInBoundsPtrAccessChain",
        "OpCopyObject",
        "OpBitcast",
        "OpPhi",
        "OpSelect",
    }
    # First close only pointer-producing instructions. Loads and arithmetic are
    # intentionally excluded here: they are values, not alternate b55 pointers.
    pointer_ids = set(binding_ids)
    changed = True
    while changed:
        changed = False
        for result_id, opcode, operands, _line in parsed:
            if (
                result_id
                and result_id not in pointer_ids
                and opcode in pointer_ops
                and any(item in pointer_ids for item in operands)
            ):
                pointer_ids.add(result_id)
                changed = True

    stores = [
        (result_id, opcode, operands, line)
        for result_id, opcode, operands, line in parsed
        if opcode == "OpStore" and operands and operands[0] in pointer_ids
    ]
    if not stores:
        raise ValueError("binding 55 store missing after pointer closure")

    # Seed both the pointer chain and every stored value, then walk backwards
    # through defining instructions. This prevents a projection from passing
    # when only the b55 pointer is unchanged but its published value changes.
    needed_ids = set(pointer_ids)
    for _result_id, _opcode, operands, _line in stores:
        if len(operands) >= 2:
            needed_ids.add(operands[1])
    definitions = {
        result_id: (result_id, opcode, operands, line)
        for result_id, opcode, operands, line in parsed
        if result_id is not None
    }
    selected_lines = {line for _result_id, _opcode, _operands, line in stores}
    changed = True
    while changed:
        changed = False
        for identifier in tuple(needed_ids):
            instruction = definitions.get(identifier)
            if instruction is None:
                continue
            _result_id, _opcode, operands, line = instruction
            if line not in selected_lines:
                selected_lines.add(line)
                changed = True
            for operand in operands:
                if operand.startswith("%") and operand not in needed_ids:
                    needed_ids.add(operand)
                    changed = True
    for _result_id, opcode, operands, line in parsed:
        if opcode == "OpDecorate" and len(operands) >= 3 and operands[-2:] == ["Binding", "55"]:
            selected_lines.add(line)
        elif opcode == "OpDecorate" and operands and operands[0] in binding_ids and operands[-1] == "0":
            selected_lines.add(line)
    selected = [line for _result_id, _opcode, _operands, line in parsed if line in selected_lines]
    ids: dict[str, str] = {}
    next_id = 1
    normalized: list[str] = []
    for line in selected:
        def replace(match: re.Match[str]) -> str:
            nonlocal next_id
            value = match.group(0)
            if value not in ids:
                ids[value] = f"%{next_id}"
                next_id += 1
            return ids[value]
        normalized.append(re.sub(r"%[A-Za-z_][A-Za-z0-9_]*|%\d+", replace, line))
    return tuple(normalized)


def _mutants(product: str) -> list[tuple[str, str]]:
    gate_anchor = "if (!PRODUCT_B62_DIAGNOSTICS_ENABLED) return;"
    palette_start = product.find("void product_capture_terrain_palette_decision")
    gate_position = product.find(gate_anchor, palette_start)
    if palette_start < 0 or gate_position < 0:
        raise ValueError("capture gate mutation anchor missing")
    gate = product[:gate_position] + "if (PRODUCT_B62_DIAGNOSTICS_ENABLED) return;" + product[gate_position + len(gate_anchor):]
    schema = product.replace("uvec4 r4; // edge admitted, reason bits, floatBits(score), post-edge glyph", "vec4 r4; // edge admitted, reason bits, floatBits(score), post-edge glyph", 1)
    paint_anchor = "product_capture_edge_post_glyph = post_edge_glyph;"
    paint = product.replace(paint_anchor, paint_anchor + "\n\trecord.r0.z = post_edge_glyph;", 1)
    return [("capture-gate", gate), ("b62-schema", schema), ("paint-isolation", paint)]


def _live_b55_mutant(product: str) -> str:
    """Change one value that reaches the sole b55 publication store."""

    anchor = "\t\tfinalized_probe_subcell_products.products[final_index] =\n\t\t\tstaged_final_records[slot];"
    if product.count(anchor) != 1:
        raise ValueError("live b55 mutation anchor is not unique")
    replacement = "\t\tfinalized_probe_subcell_products.products[final_index] =\n\t\t\tvsb_product_failure_final_record();"
    return product.replace(anchor, replacement, 1)


def main() -> int:
    checks = Checks()
    identity_mode = "not-run"
    product = _expand_includes(PRODUCT_PATH.read_text(encoding="utf-8"))
    host = HOST_PATH.read_text(encoding="utf-8")
    baseline = _git_baseline()

    errors = contract_errors(product, host, baseline)
    checks.names.extend([
        "b62-schema-and-r0-byte-12",
        "secondary-evidence-gate",
        "protected-baseline-function-bodies",
        "same-request-final-index-join",
        "capture-paint-admission-isolation",
    ])
    checks.failures.extend(errors)

    mutant_results: list[str] = []
    for name, mutant in _mutants(product):
        mutant_errors = contract_errors(mutant, host, baseline)
        checks.names.append(f"mutant-{name}")
        if not mutant_errors:
            checks.failures.append(f"mutant-{name}: escaped contract")
            mutant_results.append(f"{name}=ESCAPED")
        else:
            mutant_results.append(f"{name}=rejected({len(mutant_errors)})")

    compiler = shutil.which("glslangValidator")
    optimizer = shutil.which("spirv-opt")
    if compiler is None or optimizer is None:
        checks.failures.append("compiler-tools: glslangValidator and spirv-opt are required")
    else:
        with tempfile.TemporaryDirectory(prefix="fl4231-f13-contract-") as directory_name:
            directory = Path(directory_name)
            current_ok, current_spv, current_detail = _optimized(product, "current", directory)
            baseline_ok, baseline_spv, baseline_detail = _optimized(baseline, "head", directory)
            checks.names.extend(["current-glslang-and-spirv-opt", "head-glslang-and-spirv-opt"])
            if not current_ok:
                checks.failures.append(f"current-compile: {current_detail}")
            if not baseline_ok:
                checks.failures.append(f"head-compile: {baseline_detail}")
            if current_ok and baseline_ok:
                checks.names.append("flags-off-normalized-module-identity")
                if current_spv == baseline_spv:
                    identity_mode = "whole-module"
                else:
                    identity_mode = "b55-output-projection"
                try:
                    current_projection = _b55_projection(_disassemble(current_spv, directory, "current"))
                    baseline_projection = _b55_projection(_disassemble(baseline_spv, directory, "head"))
                except (OSError, RuntimeError, ValueError) as error:
                    checks.failures.append(f"flags-off-b55-projection: {error}")
                else:
                    checks.names.append("flags-off-b55-output-dataflow-identity")
                    if current_projection != baseline_projection:
                        checks.failures.append("flags-off SPIR-V differs in normalized b55 output dataflow")
                # Whole-module equality is strongest; the explicit b55
                # projection remains independently executed and counted.

                checks.names.append("mutant-live-b55-flags-off-spirv-identity")
                try:
                    live_mutant = _live_b55_mutant(product)
                    live_ok, live_spv, live_detail = _optimized(live_mutant, "live-b55-mutant", directory)
                    if not live_ok:
                        checks.failures.append(f"live-b55-mutant-compile: {live_detail}")
                        mutant_results.append("live-b55=compile-failed")
                    else:
                        current_projection = _b55_projection(_disassemble(current_spv, directory, "current-live"))
                        mutant_projection = _b55_projection(_disassemble(live_spv, directory, "mutant-live"))
                        if current_projection == mutant_projection:
                            checks.failures.append("live b55 value mutant escaped normalized SPIR-V identity check")
                            mutant_results.append("live-b55=ESCAPED")
                        else:
                            mutant_results.append("live-b55=rejected-by-spirv")
                except (OSError, RuntimeError, ValueError) as error:
                    checks.failures.append(f"live-b55-mutant-identity: {error}")
                    mutant_results.append("live-b55=identity-check-error")

    status = "PASS" if not checks.failures else "FAIL"
    limitation = "" if identity_mode == "whole-module" else " limitation=whole-module-diff-isolated-to-b55-output-projection"
    print(f"FL4231_F13_CAPTURE_ONLY_CONTRACT_{status} baseline={BASELINE_PRODUCT_BLOB} checks={len(checks.names)} identity={identity_mode} mutants={';'.join(mutant_results)}{limitation}")
    if checks.failures:
        for failure in checks.failures:
            print(f"  - {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

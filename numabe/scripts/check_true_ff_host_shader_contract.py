#!/usr/bin/env python3
"""Offline True FF host/shader ABI gate (FL-4516).

This check stops before Metal.  It compiles every program owned by
TrueFFFeaturePasses.gd to SPIR-V, reflects its descriptor bindings and push
constant size, then compares those declarations with the GDScript host ABI.
It also compares constants that are duplicated across the host/shader seam.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import fl4895_glsl_check as glsl  # noqa: E402


RESOURCE_KINDS = {
    "ubos": "uniform_buffer",
    "ssbos": "storage_buffer",
    "textures": "sampler_with_texture",
    "separate_images": "texture",
    "separate_samplers": "sampler",
    "images": "image",
}

UNIFORM_KINDS = {
    "RenderingDevice.UNIFORM_TYPE_STORAGE_BUFFER": "storage_buffer",
    "RenderingDevice.UNIFORM_TYPE_UNIFORM_BUFFER": "uniform_buffer",
    "RenderingDevice.UNIFORM_TYPE_SAMPLER_WITH_TEXTURE": "sampler_with_texture",
    "sampled": "sampler_with_texture",
}


def _path_constants(source: str) -> dict[str, str]:
    return dict(re.findall(
        r'^const\s+(\w+_PATH)\s*:=\s*"res://([^\"]+\.glsl)"', source, re.M))


def _resolve_path_expression(source: str, expression: str, constants: dict[str, str],
                             stack: tuple[str, ...]) -> str:
    literal = re.fullmatch(r'"res://([^\"]+\.glsl)"', expression)
    if literal:
        return literal.group(1)
    if expression in constants:
        return constants[expression]
    indexed = re.fullmatch(r"(\w+_PATHS)\[(\d+)\]", expression)
    if indexed:
        map_name, index_text = indexed.groups()
        resolved = _path_map(source, map_name, constants, stack)
        index = int(index_text)
        if index in resolved:
            return resolved[index]
    raise ValueError(f"unresolved path expression: {expression}")


def _path_map(source: str, name: str, constants: dict[str, str],
              stack: tuple[str, ...] = ()) -> dict[int, str]:
    if name in stack:
        raise ValueError(f"recursive program map declaration: {' -> '.join((*stack, name))}")
    block = re.search(rf"^const {re.escape(name)}\s*:=\s*\{{(.*?)^\}}",
                      source, re.M | re.S)
    if not block:
        raise ValueError(f"program map declaration missing: {name}")
    resolved: dict[int, str] = {}
    entries = re.findall(
        r'(\d+)\s*:\s*("res://[^\"]+\.glsl"|\w+_PATHS?\[\d+\]|\w+_PATH)',
        block.group(1),
    )
    for index_text, expression in entries:
        index = int(index_text)
        if index in resolved:
            raise ValueError(f"{name} repeats stage {index}")
        try:
            resolved[index] = _resolve_path_expression(
                source, expression, constants, (*stack, name))
        except ValueError as exc:
            raise ValueError(f"{name}[{index}] {exc}") from exc
    if not resolved:
        raise ValueError(f"program map is empty: {name}")
    return resolved


def _program_paths(host: str) -> list[str]:
    """Resolve the actual PROGRAMS compile inventory, and require it to be exact."""
    constants = _path_constants(host)
    maps = {name: _path_map(host, name, constants)
            for name in ("EXTRACT_KERNELS", "OWED_KERNELS", "TRAVERSE_KERNELS")}
    paths: list[str] = []
    for item in _assigned_array(host, r"^const PROGRAMS[^=]*="):
        indexed = re.fullmatch(r"(\w+_PATHS|\w+_KERNELS)\[(\d+)\]", item)
        if indexed:
            name, index_text = indexed.groups()
            index = int(index_text)
            path_map = maps.get(name)
            if path_map is None:
                path_map = _path_map(host, name, constants)
            if index not in path_map:
                raise ValueError(f"PROGRAMS has unresolved entry: {item}")
            paths.append(path_map[index])
        elif item in constants:
            paths.append(constants[item])
        else:
            raise ValueError(f"PROGRAMS has unresolved entry: {item}")
    duplicates = sorted({path for path in paths if paths.count(path) > 1})
    if duplicates:
        raise ValueError(f"PROGRAMS repeats paths: {duplicates}")
    declared = set(constants.values()) | set(re.findall(r'"res://([^\"]+\.glsl)"', host))
    omitted = sorted(declared - set(paths))
    surplus = sorted(set(paths) - declared)
    if omitted or surplus:
        raise ValueError(f"PROGRAMS inventory mismatch: omitted={omitted} surplus={surplus}")
    return paths


def _integer_list(text: str) -> set[int]:
    return {int(value) for value in re.findall(r"\b\d+\b", text)}


def _dispatch_coverage_errors(host: str) -> list[str]:
    """Require every compiled program to have a production dispatch route."""
    constants = _path_constants(host)
    maps = {name: _path_map(host, name, constants)
            for name in ("EXTRACT_KERNELS", "OWED_KERNELS", "TRAVERSE_KERNELS")}
    first_function = re.search(r"^func ", host, re.M)
    if not first_function:
        return ["True FF host declares no functions"]
    body = host[first_function.start():]
    reached: dict[str, set[int]] = {name: set() for name in maps}

    for name in ("EXTRACT_KERNELS", "TRAVERSE_KERNELS"):
        reached[name] |= {int(value) for value in re.findall(
            rf"{name}\[(\d+)\]", body)}
    if "TRAVERSE_KERNELS[T_ADJACENCY]" in body:
        adjacency = re.search(r"^const T_ADJACENCY\s*:=\s*(\d+)", host, re.M)
        if not adjacency:
            return ["T_ADJACENCY dispatch alias is unresolved"]
        reached["TRAVERSE_KERNELS"].add(int(adjacency.group(1)))

    # These helpers select map entries through a stage/operation loop variable.
    for match in re.finditer(r"for\s+(kernel|operation)\s+in\s*\[", body):
        values = _integer_list(_bracket_contents(body, body.find("[", match.start())))
        tail = body[match.end():body.find("\n\n", match.end())]
        if "TRAVERSE_KERNELS[kernel]" in tail:
            reached["TRAVERSE_KERNELS"] |= values
        if "EXTRACT_KERNELS[operation]" in tail:
            reached["EXTRACT_KERNELS"] |= values
    reached["TRAVERSE_KERNELS"] |= {int(value) for value in re.findall(
        r"_dispatch_witness_lanes\(list,\s*(\d+)", body)}

    reached["OWED_KERNELS"] |= {int(value) for value in re.findall(
        r"_dispatch_owed\(list,\s*(\d+)", body)}
    for match in re.finditer(r"for\s+stage\s+in\s*\[", body):
        values = _integer_list(_bracket_contents(body, body.find("[", match.start())))
        tail = body[match.end():body.find("\n\n", match.end())]
        if "_dispatch_owed(list, stage" in tail:
            reached["OWED_KERNELS"] |= values

    errors: list[str] = []
    for name, entries in maps.items():
        missing = sorted(set(entries) - reached[name])
        unknown = sorted(reached[name] - set(entries))
        if missing or unknown:
            errors.append(f"{name} dispatch coverage mismatch: "
                          f"unreachable={missing} unknown={unknown}")

    mapped_paths = {path for entries in maps.values() for path in entries.values()}
    for constant, path in constants.items():
        if path not in mapped_paths and not re.search(rf"\b{re.escape(constant)}\b", body):
            errors.append(f"compiled direct program has no production route: {path}")
    return errors


def _function_body(source: str, name: str) -> str:
    match = re.search(rf"^(?:static )?func {re.escape(name)}\([^\n]*", source, re.M)
    if not match:
        raise ValueError(f"host function missing: {name}")
    following = source.find("\nfunc ", match.end())
    return source[match.start():following if following >= 0 else len(source)]


def _delimited_contents(source: str, start: int, opener: str, closer: str) -> str:
    if start < 0 or start >= len(source) or source[start] != opener:
        raise ValueError(f"expected opening delimiter: {opener}")
    depth = 0
    quoted = False
    escaped = False
    for index in range(start, len(source)):
        char = source[index]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return source[start + 1:index]
    raise ValueError(f"unterminated delimited expression: {opener}{closer}")


def _bracket_contents(source: str, start: int) -> str:
    return _delimited_contents(source, start, "[", "]")


def _array_items(contents: str) -> list[str]:
    items: list[str] = []
    start = 0
    depth = 0
    quoted = False
    escaped = False
    for index, char in enumerate(contents):
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            item = contents[start:index].strip()
            if item:
                items.append(item)
            start = index + 1
    item = contents[start:].strip()
    if item:
        items.append(item)
    return items


def _assigned_array(source: str, assignment_pattern: str) -> list[str]:
    match = re.search(assignment_pattern, source, re.M)
    if not match:
        raise ValueError(f"array declaration missing: {assignment_pattern}")
    bracket = source.find("[", match.end())
    contents = _bracket_contents(source, bracket)
    # GDScript comments are not array entries. Strip comment-only lines before
    # splitting so commas in inventory annotations cannot become fake paths.
    contents = re.sub(r"(?m)^\s*#.*(?:\n|$)", "", contents)
    return _array_items(contents)


def _literal_call_arrays(source: str, name: str, argument: int) -> list[list[str]]:
    arrays: list[list[str]] = []
    for match in re.finditer(rf"\b{re.escape(name)}\(", source):
        opening = source.find("(", match.start())
        args = _array_items(_delimited_contents(source, opening, "(", ")"))
        if len(args) <= argument:
            continue
        value = args[argument].strip()
        if value.startswith("["):
            arrays.append(_array_items(_bracket_contents(value, 0)))
    return arrays


def _string_constant(source: str, name: str) -> list[str]:
    items = _assigned_array(source, rf"^const {re.escape(name)}[^=]*=")
    values: list[str] = []
    for item in items:
        match = re.fullmatch(r'"([^"]+)"', item)
        if not match:
            raise ValueError(f"{name} contains a non-literal entry: {item}")
        values.append(match.group(1))
    return values


def _resolved_path_array(source: str, name: str) -> set[str]:
    constants = _path_constants(source)
    items = _assigned_array(source, rf"^const {re.escape(name)}[^=]*=")
    resolved: set[str] = set()
    for item in items:
        indexed = re.fullmatch(r"(\w+_PATHS|\w+_KERNELS)\[(\d+)\]", item)
        if indexed:
            map_name, index_text = indexed.groups()
            stage = int(index_text)
            path_map = _path_map(source, map_name, constants)
            if stage not in path_map:
                raise ValueError(f"unresolved {name} map entry: {item}")
            resolved.add(path_map[stage])
            continue
        if item not in constants:
            raise ValueError(f"unresolved {name} entry: {item}")
        resolved.add(constants[item])
    return resolved


def _integer_constants(source: str, prefix: str) -> dict[str, int]:
    return {name: int(value) for name, value in re.findall(
        rf"^const ({re.escape(prefix)}\w*)[^=]*=\s*(\d+)\s*$", source, re.M)}


def _live_types(project: Path, enabled_families: set[str]) -> dict[int, str]:
    source = (project / "render/ascii/features/TrueFFLiveSourceBindings.gd").read_text(
        encoding="utf-8")
    bindings = _string_constant(source, "BINDINGS")
    storage = set(_string_constant(source, "STORAGE_BINDINGS"))
    water = set(_string_constant(source, "WATER_KEYS"))
    effects = set(_string_constant(source, "EFFECT_KEYS"))
    unknown = set(bindings) - water - effects - {"sky"}
    if unknown or set(storage) - set(bindings):
        raise ValueError(f"unclassified live binding keys: {sorted(unknown)}")
    if "uniform.binding = binding" not in source or "key in STORAGE_BINDINGS" not in source:
        raise ValueError("live binding/type publication no longer has the audited structure")
    enabled_keys: set[str] = set()
    if "WATER" in enabled_families:
        enabled_keys |= water
    if "EFFECTS" in enabled_families:
        enabled_keys |= effects
    if "SKY" in enabled_families:
        enabled_keys.add("sky")
    return {index: ("storage_buffer" if key in storage else "uniform_buffer")
            for index, key in enumerate(bindings) if key in enabled_keys}


def _vegetation_types(project: Path) -> dict[int, str]:
    feature_source = (project / "render/ascii/features/TrueFFVegetationFeatures.gd").read_text(
        encoding="utf-8")
    feature_constants = _integer_constants(feature_source, "BINDING_")
    expected_features = {"BINDING_FEATURES", "BINDING_LOCAL_ROWS",
                         "BINDING_INSTANCES", "BINDING_ASSETS"}
    if set(feature_constants) != expected_features \
            or "uniform.uniform_type = RenderingDevice.UNIFORM_TYPE_STORAGE_BUFFER" not in feature_source:
        raise ValueError("vegetation feature binding publication no longer has the audited structure")
    types = {binding: "storage_buffer" for binding in feature_constants.values()}

    runtime_source = (project / "render/effects/vegetation/VegetationRuntime.gd").read_text(
        encoding="utf-8")
    runtime = _integer_constants(runtime_source, "BINDING_")
    required = {"BINDING_DISPLACEMENT", "BINDING_FRAME", "BINDING_BANK_FIRST",
                "BINDING_BANK_LAST", "BINDING_COUNTS"}
    if not required <= runtime.keys() \
            or "uniform.uniform_type = RenderingDevice.UNIFORM_TYPE_SAMPLER_WITH_TEXTURE" not in runtime_source \
            or "return _u_ubo(BINDING_FRAME" not in runtime_source \
            or "binding == BINDING_COUNTS else _u_storage" not in runtime_source:
        raise ValueError("vegetation runtime binding publication no longer has the audited structure")
    types[runtime["BINDING_DISPLACEMENT"]] = "sampler_with_texture"
    types[runtime["BINDING_FRAME"]] = "uniform_buffer"
    for binding in range(runtime["BINDING_BANK_FIRST"], runtime["BINDING_BANK_LAST"] + 1):
        types[binding] = ("uniform_buffer" if binding == runtime["BINDING_COUNTS"]
                          else "storage_buffer")
    return types


def _normal_set0_types(project: Path, host: str, path: str,
                       water_enabled: bool) -> dict[int, str]:
    body = _function_body(host, "_ensure_sets")
    frame_signature = _function_body(host, "_frame_signature")
    vegetation_validation = _function_body(host, "_vegetation_uniforms_valid")
    required_shapes = ("terrain.size() != 4", "surface.size() != 2",
                       "_vegetation_uniforms_valid(vegetation", "for i in range(4)",
                       "_uniform(i, RenderingDevice.UNIFORM_TYPE_STORAGE_BUFFER",
                       "for i in range(2)", "_uniform(37 + i",
                       "_uniform(27 + i", "uniforms.append_array(vegetation_uniforms)")
    missing = [fragment for fragment in required_shapes
               if fragment not in body and fragment not in frame_signature]
    if missing:
        raise ValueError(f"host set construction changed; missing {missing}")
    vegetation_shapes = ("values.size() != 12", "value is RDUniform",
                         "ids.is_empty()", "rid.is_valid()")
    missing_vegetation = [fragment for fragment in vegetation_shapes
                          if fragment not in vegetation_validation]
    if missing_vegetation:
        raise ValueError(
            "host vegetation validation changed; missing "
            f"{missing_vegetation}"
        )

    types = {binding: "storage_buffer" for binding in range(4)}
    order_match = re.search(r"var buffer_order: Dictionary = \{(.*?)\}", body, re.S)
    if not order_match:
        raise ValueError("buffer_order declaration missing")
    order = {int(binding): name for binding, name in re.findall(
        r'(\d+):\s*"([^"]+)"', order_match.group(1))}
    if not order:
        raise ValueError("buffer_order declaration is empty")
    if 'name in ["camera", "scene_info"]' not in body:
        raise ValueError("buffer_order type rule changed")
    for binding, name in order.items():
        types[binding] = "uniform_buffer" if name in {"camera", "scene_info"} \
            else "storage_buffer"

    literal_pattern = (r"_uniform\((\d+),\s*"
                       r"(sampled|RenderingDevice\.UNIFORM_TYPE_[A-Z_]+)")
    for binding_text, kind_text in re.findall(literal_pattern, body):
        if kind_text not in UNIFORM_KINDS:
            raise ValueError(f"unknown host uniform kind: {kind_text}")
        types[int(binding_text)] = UNIFORM_KINDS[kind_text]

    wrapper_source = (project / path).read_text(encoding="utf-8")
    witness_paths = _resolved_path_array(host, "WITNESS_KERNEL_PATHS")
    wrapper_is_witness = "#define TFF_WITNESS_KERNEL" in wrapper_source
    if (path in witness_paths) != wrapper_is_witness:
        raise ValueError(f"witness descriptor policy disagrees with shader wrapper: {path}")
    if path not in witness_paths:
        types.pop(66, None)

    # Cardinalities and types are established by the checked frame signature
    # and the loop bodies above. These entries therefore come from host code,
    # not a shader-side binding inventory copied into this checker.
    types.update({22: "storage_buffer", 23: "sampler_with_texture",
                  24: "sampler_with_texture", 25: "uniform_buffer",
                  27: "storage_buffer", 28: "storage_buffer",
                  37: "storage_buffer", 38: "storage_buffer"})
    types.update(_vegetation_types(project))

    name = Path(path).name
    if not water_enabled:
        types.pop(39, None)
    if name != "true_ff_feature_scene.glsl":
        for binding in range(50, 54):
            types.pop(binding, None)
    else:
        if "for binding in [51, 52, 53]" not in body:
            raise ValueError("scene binding loop changed")
        types.update({binding: "storage_buffer" for binding in range(51, 54)})
    declares_native_control = (
        '#include "res://render/ascii/features/true_ff_feature_traverse.glslinc"'
        in wrapper_source)
    if not declares_native_control:
        types.pop(60, None)
    if name.startswith("true_ff_feature_owed"):
        if "for binding in [60, 61, 62, 63]" not in body:
            raise ValueError("owed binding loop changed")
        types.update({binding: "storage_buffer" for binding in range(60, 64)})
    else:
        for binding in range(61, 64):
            types.pop(binding, None)
    return types


def _discovery_set0_types(host: str) -> dict[int, str]:
    body = _function_body(host, "_ensure_discovery_set")
    resources = _assigned_array(body, r"var resources:\s*Array\s*=")
    if "for binding in range(resources.size())" not in body \
            or "binding in [10, 11]" not in body:
        raise ValueError("discovery binding publication no longer has the audited structure")
    return {binding: ("uniform_buffer" if binding in {10, 11} else "storage_buffer")
            for binding in range(len(resources))}


def _native_set0_types(host: str) -> dict[int, str]:
    body = _function_body(host, "_ensure_native_set")
    initial = _assigned_array(body, r"var resources:\s*Array\s*=")
    fields = _assigned_array(body, r"for field in")
    appended = _assigned_array(body, r"resources\.append_array\(")
    buffer_count = len(initial) + len(fields) + len(appended)
    if "binding if binding < 16 else 20 + binding - 16" not in body:
        raise ValueError("native binding remap changed")
    types = {(binding if binding < 16 else 20 + binding - 16): "storage_buffer"
             for binding in range(buffer_count)}
    sampler = re.search(r"_uniform\((\d+),\s*RenderingDevice\.UNIFORM_TYPE_SAMPLER_WITH_TEXTURE", body)
    if not sampler:
        raise ValueError("native atlas binding missing")
    types[int(sampler.group(1))] = "sampler_with_texture"
    return types


def _host_types(project: Path, host: str, path: str,
                enabled_families: set[str]) -> dict[tuple[int, int], str]:
    name = Path(path).name
    if name == "true_ff_feature_discover.glsl":
        set0 = _discovery_set0_types(host)
        live = _live_types(project, enabled_families & {"WATER", "SKY"})
    elif name == "true_ff_feature_native.glsl":
        set0 = _native_set0_types(host)
        live = {}
    else:
        set0 = _normal_set0_types(project, host, path, "WATER" in enabled_families)
        live = _live_types(project, enabled_families)
    return ({(0, binding): kind for binding, kind in set0.items()}
            | {(1, binding): kind for binding, kind in live.items()})


def _reflected_resources(reflection: dict) -> dict[tuple[int, int], str]:
    found: dict[tuple[int, int], str] = {}
    for group, kind in RESOURCE_KINDS.items():
        for row in reflection.get(group, []):
            key = (int(row.get("set", 0)), int(row["binding"]))
            previous = found.get(key)
            if previous is not None and previous != kind:
                raise ValueError(f"reflection declares two resource kinds at set/binding {key}")
            found[key] = kind
    return found


def _reflect(source: str, stage: str, glslang: str, spirv_cross: str) -> tuple[dict, str]:
    with tempfile.TemporaryDirectory(prefix="true-ff-host-shader-") as directory:
        work = Path(directory)
        shader = work / f"shader.{glsl.STAGES[stage]}"
        spv = work / "shader.spv"
        shader.write_text("#version 450\n" + source, encoding="utf-8")
        built = subprocess.run(
            [glslang, "-V", "--target-env", "vulkan1.1", "-S", glsl.STAGES[stage],
             "-o", str(spv), str(shader)], capture_output=True, text=True)
        if built.returncode != 0:
            return {}, "glslang: " + (built.stdout + built.stderr)[-3000:]
        reflected = subprocess.run([spirv_cross, str(spv), "--reflect"],
                                   capture_output=True, text=True)
        if reflected.returncode != 0:
            return {}, "spirv-cross --reflect: " + (reflected.stdout + reflected.stderr)[-3000:]
        try:
            return json.loads(reflected.stdout), ""
        except ValueError as exc:
            return {}, f"invalid reflection JSON: {exc}"


def _packed_array_count(body: str, prefix: str) -> int:
    match = re.search(prefix, body)
    if not match:
        raise ValueError(f"push construction missing: {prefix}")
    bracket = body.find("[", match.end())
    return len(_array_items(_bracket_contents(body, bracket)))


def _expected_push_bytes(host: str, path: str) -> int:
    name = Path(path).name
    if name == "true_ff_feature_native.glsl":
        body = _function_body(host, "_dispatch_native_certificate")
        patterns = [r"var push:[^=]*=\s*PackedInt32Array\(",
                    r"push\.append_array\(PackedFloat32Array\(",
                    r"push\.append_array\(PackedInt32Array\(",
                    r"push\.append_array\(PackedInt32Array\("]
        cursor = 0
        total = 0
        for pattern in patterns:
            match = re.search(pattern, body[cursor:])
            if not match:
                raise ValueError("native push construction changed")
            absolute = cursor + match.start()
            total += _packed_array_count(body[absolute:], pattern)
            cursor += match.end()
        return total * 4
    if name.startswith("true_ff_feature_owed"):
        body = _function_body(host, "_dispatch_owed")
        patterns = [r"var push:[^=]*=\s*PackedInt32Array\(",
                    r"push\.append_array\(PackedFloat32Array\(",
                    r"push\.append_array\(PackedInt32Array\(",
                    r"push\.append_array\(PackedInt32Array\("]
        cursor = 0
        total = 0
        for pattern in patterns:
            match = re.search(pattern, body[cursor:])
            if not match:
                raise ValueError("owed push construction changed")
            absolute = cursor + match.start()
            total += _packed_array_count(body[absolute:], pattern)
            cursor += match.end()
        return total * 4
    bind = _function_body(host, "_bind")
    if "PackedInt32Array(push).to_byte_array()" not in bind:
        raise ValueError("common push serialization changed")
    literal_pushes = (_literal_call_arrays(host, "_bind", 2)
                      + _literal_call_arrays(host, "_dispatch_indirect", 2)
                      + _literal_call_arrays(host, "_dispatch_host_count", 2))
    if not literal_pushes or any(len(items) != 4 for items in literal_pushes):
        raise ValueError("common dispatch push arrays are not uniformly four int32 values")
    return 16


def _contract_errors(identity: str, required: dict[tuple[int, int], str],
                     supplied: dict[tuple[int, int], str], push: int,
                     expected_push: int) -> list[str]:
    errors: list[str] = []
    for key, kind in sorted(required.items()):
        if key not in supplied:
            errors.append(f"{identity}: shader requires set={key[0]} binding={key[1]} "
                          "that the host does not supply")
        elif supplied[key] != kind:
            errors.append(f"{identity}: set={key[0]} binding={key[1]} type mismatch "
                          f"host={supplied[key]} shader={kind}")
    for key in sorted(supplied.keys() - required.keys()):
        errors.append(f"{identity}: host supplies undeclared set={key[0]} binding={key[1]} "
                      f"type={supplied[key]}")
    if push != expected_push:
        errors.append(f"{identity}: push constant size mismatch "
                      f"host={expected_push} shader={push}")
    return errors


def _constant_errors(project: Path, host: str) -> list[str]:
    shader = (project / "render/ascii/features/true_ff_shadow_admission.glslinc").read_text(
        encoding="utf-8")
    host_match = re.search(r"^const SHADOW_ACCOUNT_ADVANCE\s*:=\s*([0-9.]+)", host, re.M)
    shader_match = re.search(
        r"^const float TFF_SHADOW_ACCOUNT_ADVANCE\s*=\s*([0-9.]+)", shader, re.M)
    if not host_match or not shader_match:
        return ["shared shadow-account advance declaration missing on host or shader side"]
    if float(host_match.group(1)) != float(shader_match.group(1)):
        return ["shared shadow-account advance mismatch: "
                f"host={host_match.group(1)} shader={shader_match.group(1)}"]
    return []


def check(project: Path, start: int = 0, count: int | None = None) -> tuple[list[str], int, int]:
    host_path = project / "render/ascii/features/TrueFFFeaturePasses.gd"
    if not host_path.is_file():
        return [f"missing host owner: {host_path}"], 0, 0
    host = host_path.read_text(encoding="utf-8")
    try:
        all_programs = _program_paths(host)
    except ValueError as exc:
        return [f"invalid True FF program inventory: {exc}"], 0, 0
    if not all_programs:
        return ["empty True FF program inventory"], 0, 0
    if start < 0 or (count is not None and count <= 0):
        return ["invalid program slice"], 0, 0
    programs = all_programs[start:] if count is None else all_programs[start:start + count]
    if not programs:
        return ["empty True FF program slice"], 0, 0
    missing = [path for path in programs if not (project / path).is_file()]
    if missing:
        return [f"missing program: {path}" for path in missing], len(programs), 0
    glslang = shutil.which("glslangValidator") or "/opt/homebrew/bin/glslangValidator"
    spirv_cross = shutil.which("spirv-cross") or "/opt/homebrew/bin/spirv-cross"
    if not Path(glslang).is_file() or not Path(spirv_cross).is_file():
        return ["glslangValidator and spirv-cross are required for host/shader reflection"], len(programs), 0

    old_project = glsl.PROJ
    glsl.PROJ = project
    errors = _constant_errors(project, host)
    if start == 0:
        errors.extend(_dispatch_coverage_errors(host))
    variants = 0
    try:
        for path in programs:
            for label, stage, source in glsl.metal_jobs(path):
                variants += 1
                reflection, error = _reflect(source, stage, glslang, spirv_cross)
                identity = f"{path}[{label}]"
                if error:
                    errors.append(f"{identity}: {error}")
                    continue
                withdrawn = set(re.findall(r"^#define TFF_WITHDRAWN_(WATER|EFFECTS|SKY)$",
                                           source, re.M))
                enabled = {"WATER", "EFFECTS", "SKY"} - withdrawn
                try:
                    supplied = _host_types(project, host, path, enabled)
                    required = _reflected_resources(reflection)
                    expected = _expected_push_bytes(host, path)
                except (KeyError, OSError, TypeError, ValueError) as exc:
                    errors.append(f"{identity}: invalid host/resource contract: {exc}")
                    continue
                push = glsl.push_constant_bytes(reflection)
                errors.extend(_contract_errors(identity, required, supplied, push, expected))
    finally:
        glsl.PROJ = old_project
    return errors, len(programs), variants


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=ROOT / "godot_project")
    parser.add_argument("--start", type=int, default=0,
                        help="first host program index (offline audit sharding)")
    parser.add_argument("--count", type=int,
                        help="number of host programs to inspect")
    args = parser.parse_args(argv)
    project = args.project.resolve()
    errors, programs, variants = check(project, args.start, args.count)
    for error in errors[:80]:
        print("FAIL " + error)
    if len(errors) > 80:
        print(f"FAIL ... {len(errors) - 80} additional contract error(s)")
    print("HOST_SHADER_CONTRACT_RESULT " + json.dumps({
        "project": str(project), "programs": programs, "variants": variants,
        "failed": len(errors),
    }))
    return 1 if errors or programs == 0 or variants == 0 else 0


if __name__ == "__main__":
    sys.exit(main())

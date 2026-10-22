#!/usr/bin/env python3
"""FL-4877: production True FF shaders as Metal programs for the offline frame.

Each RD shader stage goes through the exact GLSL -> SPIR-V -> MSL path of
scripts/fl4895_glsl_check.py (same include expansion, same spirv-cross MSL
options, same Vulkan 1.1 target) and the defines its GDScript host injects.
The MSL argument-buffer struct is then joined to the SPIR-V reflection so a
host binding (descriptor set, GLSL binding) resolves to its argument-buffer
[[id]]; an unmatched or ambiguous resource fails closed.

Compiled stages are cached by exact source text and toolchain identity under
.run/true_ff_launch_guard/offline_frame/msl/.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import fl4895_glsl_check as glsl  # noqa: E402

CACHE = ROOT / ".run" / "true_ff_launch_guard" / "offline_frame" / "msl"
SCHEMA = "true_ff.offline_msl.v1"
TEXTURE_GROUPS = ("textures", "separate_images", "images")


class ShaderError(RuntimeError):
    pass


def _toolchain() -> str:
    tools = glsl.metal_tools()
    if not tools:
        raise ShaderError("glslangValidator, spirv-cross and xcrun metal are required")
    digest = hashlib.sha256(SCHEMA.encode())
    for key in sorted(tools):
        digest.update(Path(tools[key]).read_bytes())
    digest.update(str(glsl.MSL_OPTIONS).encode())
    return digest.hexdigest()


_TOOLCHAIN: str | None = None


def stage_sources(res: str, defines: list[str]) -> dict[str, str]:
    """{stage: expanded source with the host's defines first} for one RD file."""
    out = {}
    for stage, source in glsl.stages(glsl.PROJ / res).items():
        out[stage] = "".join(f"#define {name}\n" for name in defines) + source
    return out


def _parse_struct_members(msl: str) -> dict[int, list[tuple[str, str, int]]]:
    """{set: [(pointee type or texture kind, member name, id)]} from spvDescriptorSetBufferN."""
    sets: dict[int, list[tuple[str, str, int]]] = {}
    for match in re.finditer(r"struct spvDescriptorSetBuffer(\d+)\s*\{(.*?)\};", msl, re.S):
        members = []
        for line in match.group(2).splitlines():
            line = line.strip()
            if not line:
                continue
            found = re.match(r"(.*?)\s+(\w+)\s*\[\[id\((\d+)\)\]\];$", line)
            if not found:
                raise ShaderError(f"unparsed argument-buffer member: {line}")
            declared, name, ident = found.groups()
            pointer = re.match(r"(?:(?:const|coherent|volatile)\s+)*(?:device|constant)\s+"
                               r"(?:(?:const|volatile)\s+)*(\w+)\s*\*", declared)
            members.append((pointer.group(1) if pointer else declared, name, int(ident)))
        sets[int(match.group(1))] = members
    return sets


def _entry_signature(msl: str) -> tuple[dict[int, int], int]:
    """({set: buffer index}, push buffer index or -1) from the main0 signature."""
    match = re.search(r"(?:kernel|vertex|fragment)\s+\w+\s+main0\((.*?)\)\s*\{", msl, re.S)
    if not match:
        raise ShaderError("main0 signature missing")
    sets: dict[int, int] = {}
    push = -1
    for argument in match.group(1).split(","):
        found = re.search(r"&\s*(\w+)\s*\[\[buffer\((\d+)\)\]\]", argument)
        if not found:
            continue
        name, index = found.group(1), int(found.group(2))
        set_match = re.fullmatch(r"spvDescriptorSet(\d+)", name)
        if set_match:
            sets[int(set_match.group(1))] = index
        elif push < 0:
            push = index
        else:
            raise ShaderError(f"unexpected second non-set buffer argument: {name}")
    return sets, push


def _binding_map(reflection: dict, members: dict[int, list[tuple[str, str, int]]]) -> dict:
    """{"set:binding": [[id, kind], ...]} joining reflection to MSL member ids."""
    resolved: dict[str, list] = {}
    types = reflection.get("types", {})
    for group in ("ssbos", "ubos"):
        for row in reflection.get(group, []):
            set_index, binding = int(row.get("set", 0)), int(row["binding"])
            block = types.get(row["type"], {}).get("name", row["name"])
            candidates = [m for m in members.get(set_index, []) if m[0] == block]
            if len(candidates) != 1:
                stripped = [m for m in members.get(set_index, [])
                            if re.sub(r"_\d+$", "", m[0]) == block]
                candidates = stripped if len(stripped) == 1 else candidates
            if len(candidates) != 1:
                raise ShaderError(f"buffer set={set_index} binding={binding} block={block}: "
                                  f"{len(candidates)} argument-buffer matches")
            resolved[f"{set_index}:{binding}"] = [[candidates[0][2], "buffer"]]
    for group in TEXTURE_GROUPS:
        for row in reflection.get(group, []):
            set_index, binding = int(row.get("set", 0)), int(row["binding"])
            by_name = {m[1]: m for m in members.get(set_index, [])}
            texture = by_name.get(row["name"])
            if texture is None:
                raise ShaderError(f"texture set={set_index} binding={binding} {row['name']} unmatched")
            entries = [[texture[2], "texture"]]
            sampler = by_name.get(row["name"] + "Smplr")
            if group == "textures":
                if sampler is None:
                    raise ShaderError(f"combined sampler {row['name']} has no Smplr member")
                entries.append([sampler[2], "sampler"])
            resolved[f"{set_index}:{binding}"] = entries
    for row in reflection.get("separate_samplers", []):
        set_index, binding = int(row.get("set", 0)), int(row["binding"])
        by_name = {m[1]: m for m in members.get(set_index, [])}
        if row["name"] not in by_name:
            raise ShaderError(f"sampler {row['name']} unmatched")
        resolved[f"{set_index}:{binding}"] = [[by_name[row["name"]][2], "sampler"]]
    claimed = sorted((key.split(":")[0], entry[0]) for key, set_rows in resolved.items()
                     for entry in set_rows)
    declared = sorted((str(set_index), m[2]) for set_index, rows in members.items() for m in rows)
    if len(claimed) != len(set(claimed)):
        raise ShaderError("two host bindings resolved to one argument-buffer id")
    if len(declared) != len(claimed):
        raise ShaderError(f"argument-buffer members {len(declared)} != reflected resources {len(claimed)}")
    return resolved


def compile_stage(source: str, stage: str) -> dict:
    """Compile one stage; returns {"msl", "bindings", "sets", "push_index", "push_bytes", "threads"}."""
    global _TOOLCHAIN
    if _TOOLCHAIN is None:
        _TOOLCHAIN = _toolchain()
    key = hashlib.sha256("\0".join([_TOOLCHAIN, stage, source]).encode()).hexdigest()
    folder = CACHE / key[:2]
    meta_path = folder / f"{key}.json"
    msl_path = folder / f"{key}.metal"
    lib_path = folder / f"{key}.metallib"
    if meta_path.is_file() and msl_path.is_file() and lib_path.is_file():
        meta = json.loads(meta_path.read_text())
        meta["msl"] = str(msl_path)
        meta["metallib"] = str(lib_path)
        return meta
    folder.mkdir(parents=True, exist_ok=True)
    work = folder / f"{key}.work"
    work.mkdir(exist_ok=True)
    glsl_file = work / f"shader.{glsl.STAGES[stage]}"
    spv = work / "shader.spv"
    glsl_file.write_text("#version 450\n" + source, encoding="utf-8")
    built = subprocess.run(["glslangValidator", "-V", "--target-env", "vulkan1.1", "-S",
                            glsl.STAGES[stage], "-o", str(spv), str(glsl_file)],
                           capture_output=True, text=True)
    if built.returncode != 0:
        raise ShaderError("glslang: " + "\n".join(
            line for line in (built.stdout + built.stderr).splitlines() if "ERROR" in line)[:3000])
    reflected = subprocess.run(["spirv-cross", str(spv), "--reflect"], capture_output=True, text=True)
    if reflected.returncode != 0:
        raise ShaderError("spirv-cross --reflect: " + reflected.stderr[-2000:])
    reflection = json.loads(reflected.stdout)
    options = list(glsl.MSL_OPTIONS) + (["--flip-vert-y"] if stage == "vertex" else [])
    converted = subprocess.run(["spirv-cross", str(spv), *options, "--output", str(msl_path)],
                               capture_output=True, text=True)
    if converted.returncode != 0:
        raise ShaderError("spirv-cross: " + (converted.stdout + converted.stderr)[-2000:])
    msl = msl_path.read_text(encoding="utf-8")
    sets, push = _entry_signature(msl)
    threads = [1, 1, 1]
    for entry in reflection.get("entryPoints", []):
        if entry.get("workgroup_size"):
            threads = [int(v) for v in entry["workgroup_size"]]
    meta = {"bindings": _binding_map(reflection, _parse_struct_members(msl)),
            "sets": {str(k): v for k, v in sets.items()}, "push_index": push,
            "push_bytes": glsl.push_constant_bytes(reflection), "threads": threads}
    air = work / "shader.air"
    for command in (["xcrun", "-sdk", "macosx", "metal", f"-std={glsl.METAL_STD}", "-c", str(msl_path), "-o", str(air)],
                    ["xcrun", "-sdk", "macosx", "metallib", str(air), "-o", str(lib_path)]):
        done = subprocess.run(command, capture_output=True, text=True)
        if done.returncode != 0:
            raise ShaderError("metal: " + "\n".join(line for line in done.stderr.splitlines()
                                                      if "error:" in line)[:3000])
    meta_path.write_text(json.dumps(meta, sort_keys=True), encoding="utf-8")
    meta["msl"] = str(msl_path)
    meta["metallib"] = str(lib_path)
    return meta


def compile_program(res: str, defines: list[str] | None = None) -> dict:
    """{stage: compile_stage(...)} for every stage of one RD shader file."""
    return {stage: compile_stage(source, stage)
            for stage, source in stage_sources(res, list(defines or [])).items()}


if __name__ == "__main__":
    for path in sys.argv[1:]:
        program = compile_program(path)
        for stage, meta in program.items():
            print(path, stage, "push", meta["push_bytes"], "@", meta["push_index"],
                  "sets", meta["sets"], "bindings", len(meta["bindings"]))

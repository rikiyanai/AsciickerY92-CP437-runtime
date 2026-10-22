#!/usr/bin/env python3
"""FL-4895: offline compile of Godot RD shaders with glslangValidator.

No Godot, no window, no GPU. Resolves #include "res://..." textually against a
godot_project root (GLSL_PROJ, default this checkout), drops the #[stage]
header and prepends #version 450. Exit 1 when any shader fails.

Usage: [GLSL_PROJ=<tree>/godot_project] python3 scripts/fl4895_glsl_check.py res_path [...]
       python3 scripts/fl4895_glsl_check.py --all   # every #[compute] shader under render/ascii
       python3 scripts/fl4895_glsl_check.py --metal --all
       python3 scripts/fl4895_glsl_check.py --metal res_path [...]

--metal (FL-4907 MANUAL_LAUNCH_METAL_FAILURES_FIXED_20260930): every stage of
every RD shader under render/ascii (#[compute], #[vertex], #[fragment]) goes
GLSL -> SPIR-V (glslangValidator, Vulkan 1.1) -> MSL (spirv-cross) -> AIR
(xcrun -sdk macosx metal -c). This is the path Godot 4.6's Metal driver takes
at pipeline creation, so a shader that compiles to SPIR-V but not to Metal
(for example a GLSL function named `level`, ambiguous with metal::level after
`using namespace metal`) fails here instead of at a manual launch.

MSL options mirror Godot 4.6-stable
drivers/metal/rendering_shader_container_metal.mm compile_metal_source() for a
macOS Apple-GPU device profile: MSL 3.2 (device profile msl_version on an
Apple9 GPU under macOS 15), argument buffers on at tier 2,
force_active_argument_buffer_resources, texture_buffer_native,
pad_fragment_output_components, agx_manual_cube_grad_fixup, no read-write
texture fences (MSL >= 3.2), flip_vert_y for vertex stages. Options the
spirv-cross CLI does not expose (pad_argument_buffer_resources, the r32ui
alignment function constant, Godot's explicit resource-binding table) are not
applied; they change resource indices, not name lookup or language validity.
It is a compile check, not a runtime proof.

FL-4516 backend stage: after the AIR compile, scripts/metal_backend_check.swift
compiles the same MSL on the real Metal device (MSL 3.2, fast math) and builds
one compute pipeline per specialization Godot builds (newComputePipelineState,
the GPU code generator that raised "Compiler encountered an internal error"
in Godot 4.7.2 while AIR passed). Specialization values come from their
GDScript owner (SPECIALIZATION_OWNERS); an unowned constant_id fails closed.
Residual difference from Godot: Godot's own glslang/SPIRV-Cross builds, SPIR-V
1.6 and its argument-buffer binding table generate different MSL text.

The Metal mode also prints each stage's reflected push-constant block size
(push=<bytes>), which a host must supply exactly.
"""
import concurrent.futures
import hashlib
import json
import itertools
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJ = Path(os.environ.get("GLSL_PROJ", Path(__file__).resolve().parents[1] / "godot_project"))
STAGES = {"compute": "comp", "vertex": "vert", "fragment": "frag"}
MSL_VERSION = "30200"
METAL_STD = "metal3.2"
MSL_OPTIONS = [
    "--msl", "--msl-version", MSL_VERSION,
    "--msl-argument-buffers", "--msl-argument-buffer-tier", "1",
    "--msl-force-active-argument-buffer-resources",
    "--msl-texture-buffer-native",
    "--msl-pad-fragment-output",
    "--msl-agx-manual-cube-grad-fixup",
    "--msl-no-readwrite-texture-fences",
]


def expand(path: Path) -> str:
    return expand_text(path.read_text(encoding="utf-8"))


def expand_text(text: str) -> str:
    out = []
    for line in text.splitlines():
        m = re.match(r'\s*#include\s+"res://([^"]+)"', line)
        if m:
            out.append(expand(PROJ / m.group(1)))
            continue
        stripped = line.strip()
        if stripped.startswith("#[") or stripped.startswith("#version"):
            continue
        out.append(line)
    return "\n".join(out)


def stages(path: Path) -> dict:
    """Split an RD shader file into {stage: expanded source}. A file without
    #[stage] markers is one compute stage."""
    sections: dict = {}
    current = None
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*#\[(\w+)\]\s*$", line)
        if m:
            current = m.group(1)
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(line)
    if not sections:
        return {"compute": expand(path)}
    out = {}
    for name, lines in sections.items():
        if name not in STAGES:
            continue
        out[name] = expand_text("\n".join(lines))
    return out


def metal_tools() -> dict:
    """Absolute tool paths, or {} when any is absent."""
    tools = {
        "glslang": shutil.which("glslangValidator") or "/opt/homebrew/bin/glslangValidator",
        "spirv_cross": shutil.which("spirv-cross") or "/opt/homebrew/bin/spirv-cross",
    }
    if not all(Path(p).exists() for p in tools.values()) or shutil.which("xcrun") is None:
        return {}
    probe = subprocess.run(["xcrun", "-sdk", "macosx", "metal", "--version"],
                           capture_output=True, text=True)
    if probe.returncode != 0:
        return {}
    return tools


def push_constant_bytes(reflection: dict) -> int:
    """Size of the push-constant block from spirv-cross --reflect output."""
    blocks = reflection.get("push_constants", [])
    if not blocks:
        return 0
    type_info = reflection["types"][blocks[0]["type"]]
    size = 0
    for member in type_info["members"]:
        # spirv-cross reflects offsets; the last member's end is the block size
        # for the vec4/uvec4/ivec4 members these blocks use.
        member_size = 16 if member["type"] in ("vec4", "uvec4", "ivec4") else 4
        size = max(size, int(member.get("offset", 0)) + member_size)
    return size


def metal_stage(source: str, stage: str, tools: dict, work: Path) -> tuple:
    """Returns (ok, message, push_bytes)."""
    glsl = work / f"shader.{STAGES[stage]}"
    spv = work / "shader.spv"
    msl = work / "shader.metal"
    air = work / "shader.air"
    glsl.write_text("#version 450\n" + source, encoding="utf-8")
    r = subprocess.run([tools["glslang"], "-V", "--target-env", "vulkan1.1", "-S", STAGES[stage],
                        "-o", str(spv), str(glsl)], capture_output=True, text=True)
    if r.returncode != 0:
        return False, "glslang: " + "\n".join(
            l for l in (r.stdout + r.stderr).splitlines() if "ERROR" in l)[:2000], -1
    reflect = subprocess.run([tools["spirv_cross"], str(spv), "--reflect"],
                             capture_output=True, text=True)
    push = push_constant_bytes(json.loads(reflect.stdout)) if reflect.returncode == 0 else -1
    options = list(MSL_OPTIONS) + (["--flip-vert-y"] if stage == "vertex" else [])
    r = subprocess.run([tools["spirv_cross"], str(spv), *options, "--output", str(msl)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return False, "spirv-cross: " + (r.stdout + r.stderr)[:2000], push
    r = subprocess.run(["xcrun", "-sdk", "macosx", "metal", f"-std={METAL_STD}", "-c", str(msl),
                        "-o", str(air)], capture_output=True, text=True)
    if r.returncode != 0:
        errors = [l for l in r.stderr.splitlines() if "error:" in l]
        return False, "metal: " + "\n".join(errors[:20])[:3000], push
    # FL-4516: the AIR compile does not run the GPU code generator; Godot's
    # newComputePipelineState does. Build every specialized pipeline Godot
    # builds, on the real device.
    try:
        specs = specialization_specs(source)
    except ValueError as exc:
        return False, f"specialization: {exc}", push
    try:
        r = subprocess.run([backend_tool(), str(msl), stage, *specs], capture_output=True, text=True,
                           timeout=BACKEND_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return False, (f"{BACKEND_TIMEOUT} metal backend did not build {len(specs)} pipeline(s) in "
                       f"{BACKEND_TIMEOUT_SECONDS} s; Godot builds the same pipelines at launch and "
                       "would stall or fail there"), push
    if r.returncode != 0:
        failing = [l for l in r.stdout.splitlines() if not l.startswith("OK")]
        return False, "metal backend: " + "\n".join(failing[:20])[:3000] + r.stderr[-500:], push
    return True, "", push


# A pipeline set that Apple's backend cannot build in this time is a launch
# failure: Godot creates the same pipelines during preparation (FL-4516: the
# traverse kernel ran >19 min for one variant while Godot reported
# "Compiler encountered an internal error").
BACKEND_TIMEOUT = "TIMEOUT"
BACKEND_TIMEOUT_SECONDS = int(os.environ.get("FL4895_METAL_BACKEND_TIMEOUT", "300"))


# Specialization constants Godot sets per pipeline, with the GDScript owner of
# the value list. A constant_id without an owner here fails closed.
SPECIALIZATION_OWNERS = {
    "TFF_TRAVERSE_STAGE": ("render/ascii/features/TrueFFFeaturePasses.gd", "TRAVERSE_STAGES"),
}


def specialization_specs(source: str) -> list[str]:
    found = re.findall(r"layout\s*\(\s*constant_id\s*=\s*(\d+)\s*\)\s*const\s+uint\s+(\w+)", source)
    if not found:
        return ["default"]
    if len(found) != 1:
        raise ValueError(f"multiple specialization constants not modelled: {found}")
    constant_id, name = found[0]
    if name not in SPECIALIZATION_OWNERS:
        raise ValueError(f"no value owner for specialization constant {name}")
    owner, list_name = SPECIALIZATION_OWNERS[name]
    match = re.search(rf"const {list_name} := \[([^\]]*)\]", (PROJ / owner).read_text(encoding="utf-8"))
    if not match:
        raise ValueError(f"{list_name} not found in {owner}")
    values = [v.strip() for v in match.group(1).split(",") if v.strip()]
    if not values or not all(v.isdigit() for v in values):
        raise ValueError(f"{list_name} in {owner} is not a literal uint list")
    return [f"{constant_id}={v}" for v in values]


def backend_tool() -> str:
    """Build (once per source hash) and return the Metal backend checker."""
    src = Path(__file__).resolve().parent / "metal_backend_check.swift"
    digest = hashlib.sha256(src.read_bytes()).hexdigest()[:16]
    out = Path(__file__).resolve().parents[1] / ".run" / "true_ff_launch_guard" / "bin" / f"metal_backend_check-{digest}"
    if not out.is_file():
        out.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["xcrun", "swiftc", "-O", str(src), "-o", str(out)], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError("metal_backend_check build failed: " + r.stderr[-1500:])
    return str(out)


def family_defines(source: str) -> list[tuple[str, ...]]:
    """Check every production-reachable withdrawal combination.

    Dynamic all-family programs receive all structural subsets. Family-split
    wrappers permanently define one or more withdrawals themselves; generating
    external combinations for those fixed macros recompiles identical effective
    programs and does not model any host variant.
    """
    mentioned = set(re.findall(r"\bTFF_WITHDRAWN_[A-Z_]+\b", source))
    fixed = set(re.findall(r"^\s*#define\s+(TFF_WITHDRAWN_[A-Z_]+)\b", source, re.M))
    names = sorted(mentioned - fixed)
    if len(names) > 8:
        raise ValueError("unexpected withdrawal macro inventory; review variant gate")
    return [tuple(n for n, enabled in zip(names, bits) if enabled)
            for bits in itertools.product((False, True), repeat=len(names))]


FAIL_FAST = False
VERDICT_SEMANTICS = "metal_stage.air+backend.success-cache.v2"
# Apple's backend compiler services contend heavily on the shared True FF
# programs. A host-core-sized pool converts contention into 300 s timeouts and
# does not model Godot's serial program preparation. Keep the default bounded;
# an operator may lower it further for a constrained machine.
GATE_WORKERS = int(os.environ.get("FL4895_METAL_WORKERS", str(min(4, os.cpu_count() or 4))))
if GATE_WORKERS < 1:
    raise ValueError("FL4895_METAL_WORKERS must be at least 1")
VARIANT_CACHE = Path(__file__).resolve().parents[1] / ".run" / "true_ff_launch_guard" / "metal_gate" / "variants"


def toolchain_identity(tools: dict) -> str:
    """Everything besides the shader text that decides a variant's verdict."""
    digest = hashlib.sha256()
    for key in sorted(tools):
        path = Path(tools[key])
        digest.update(key.encode() + b"\0" + (path.read_bytes() if path.is_file() else str(path).encode()))
    probe = subprocess.run(["xcrun", "-sdk", "macosx", "metal", "--version"], capture_output=True)
    digest.update(probe.stdout)
    # Verdict semantics, not this file's bytes: bump when metal_stage's
    # options or pass/fail rules change; scheduling edits keep the cache.
    digest.update(VERDICT_SEMANTICS.encode())
    digest.update(str(MSL_OPTIONS).encode() + METAL_STD.encode())
    digest.update((Path(__file__).resolve().parent / "metal_backend_check.swift").read_bytes())
    digest.update(str(BACKEND_TIMEOUT_SECONDS).encode())
    return digest.hexdigest()


def cached_metal_stage(variant: str, stage: str, tools: dict, work: Path) -> tuple:
    """metal_stage, memoized per exact variant text, specializations and toolchain.

    An unchanged shader variant is not recompiled: after one edit, a full gate
    compiles only the edited programs. Only successful verdicts are reusable:
    backend timeouts and failures may be caused by transient compiler-service
    contention and must be reproduced before they can block another launch.
    """
    if "_identity" not in tools:   # no toolchain identity, no safe cache key
        return metal_stage(variant, stage, tools, work)
    try:
        specs = specialization_specs(variant)
    except ValueError as exc:
        specs = [f"error:{exc}"]
    key = hashlib.sha256("\0".join([tools["_identity"], stage, "\x1f".join(specs), variant])
                         .encode()).hexdigest()
    cache = VARIANT_CACHE / f"{key}.json"
    if cache.is_file():
        try:
            row = json.loads(cache.read_text(encoding="utf-8"))
            if bool(row["ok"]):
                return True, str(row["message"]), int(row["push"])
        except (OSError, ValueError, KeyError, TypeError):
            pass
    ok, message, push = metal_stage(variant, stage, {k: v for k, v in tools.items() if k != "_identity"}, work)
    if ok:
        VARIANT_CACHE.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"ok": True, "message": message, "push": push}) + "\n", encoding="utf-8")
    return ok, message, push


def _one_variant(variant: str, stage: str, tools: dict) -> tuple:
    with tempfile.TemporaryDirectory() as tmp:
        return cached_metal_stage(variant, stage, tools, Path(tmp))


def metal_jobs(res: str) -> list:
    """[(label, stage, variant text)] for every stage and family variant of one shader."""
    jobs = []
    for stage, source in stages(PROJ / res).items():
        for defines in family_defines(source):
            label = stage + (":" + ",".join(defines) if defines else ":default")
            jobs.append((label, stage, "".join(f"#define {name}\n" for name in defines) + source))
    return jobs


def metal_check(res: str, tools: dict) -> list:
    """[(stage, ok, message, push_bytes)] for every stage of one shader."""
    return metal_check_all([res], tools)[res]


def metal_check_all(resources: list, tools: dict) -> dict:
    """{res: [(stage, ok, message, push_bytes)]} for every shader in `resources`.

    One pool spans all shaders: Apple's backend runs in separate compiler
    services, and a per-shader pool left most cores idle whenever a shader had
    fewer variants than workers (FL-4516). Jobs are queued in `resources`
    order, so the largest programs start first under --fail-fast.
    """
    jobs = {res: metal_jobs(res) for res in resources}
    results: dict[tuple, tuple] = {}
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=GATE_WORKERS)
    futures = {pool.submit(_one_variant, variant, stage, tools): (res, label)
               for res in resources for label, stage, variant in jobs[res]}
    try:
        for future in concurrent.futures.as_completed(futures):
            ok, message, push = future.result()
            results[futures[future]] = (ok, message, push)
            if not ok and FAIL_FAST:
                break
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return {res: [(label, *results[(res, label)]) for label, _, _ in jobs[res] if (res, label) in results]
            for res in resources}


def all_shaders(metal: bool) -> list:
    markers = ("#[compute]", "#[vertex]", "#[fragment]") if metal else ("#[compute]",)
    return sorted(str(p.relative_to(PROJ)) for p in (PROJ / "render/ascii").rglob("*.glsl")
                  if any(m in p.read_text(encoding="utf-8") for m in markers))


def main(argv) -> int:
    global PROJ
    argv = list(argv)
    if "--project" in argv:
        index = argv.index("--project")
        if index + 1 >= len(argv):
            print("FAIL --project requires a directory")
            return 2
        PROJ = Path(argv[index + 1]).resolve()
        del argv[index:index + 2]
    metal = "--metal" in argv
    global FAIL_FAST
    FAIL_FAST = "--fail-fast" in argv
    argv = [a for a in argv if a not in ("--metal", "--fail-fast")]
    if argv == ["--all"]:
        argv = all_shaders(metal)
        if not (PROJ / "project.godot").is_file():
            print(f"FAIL missing project.godot: {PROJ}")
            return 2
        # Runtime program paths are an independent required inventory. A
        # missing/truncated shader must not silently disappear from --all.
        owner = PROJ / "render/ascii/features/TrueFFFeaturePasses.gd"
        if not owner.is_file():
            print(f"FAIL missing True FF program owner: {owner}")
            return 2
        required = re.findall(r'const \w+_PATH := "res://([^"]+\.glsl)"', owner.read_text())
        if not required or set(required) - set(argv):
            print(f"FAIL incomplete True FF shader inventory: {sorted(set(required) - set(argv))}")
            return 2
    if not argv:
        print("FAIL empty shader inventory")
        return 2
    if FAIL_FAST:
        # Largest programs first: slowest to build and likeliest to fail.
        argv.sort(key=lambda res: -(PROJ / res).stat().st_size)
    bad = 0
    if metal:
        tools = metal_tools()
        if not tools:
            print("SKIP --metal: glslangValidator, spirv-cross or the xcrun metal toolchain is absent")
            return 2
        tools = {**tools, "_identity": toolchain_identity(tools)}
        checked = 0
        everything = metal_check_all(argv, tools)
        stopped = FAIL_FAST and any(not row[1] for rows in everything.values() for row in rows)
        for res in argv:
            rows, expected = everything[res], len(metal_jobs(res))
            if not expected:
                print(f"FAIL no shader stages: {res}")
                bad += 1
            for stage, ok, message, push in rows:
                checked += 1
                bad += not ok
                print(("OK   " if ok else "FAIL ") + f"{res} [{stage}] push={push}", flush=True)
                if not ok:
                    print(message, flush=True)
            if stopped and len(rows) < expected:
                print(f"NOT RUN {expected - len(rows)} variant(s) of {res}: --fail-fast stopped the gate", flush=True)
        if stopped:
            print("FAIL_FAST stopped after the first failing variant", flush=True)
        print("METAL_GATE_RESULT " + json.dumps({"project": str(PROJ.resolve()),
              "shaders": len(argv), "variants": checked, "failed": bad}))
        return 1 if bad or not checked else 0
    for res in argv:
        src = "#version 450\n" + expand(PROJ / res)
        with tempfile.NamedTemporaryFile("w", suffix=".comp", delete=False) as f:
            f.write(src)
        r = subprocess.run(["glslangValidator", "-V", "-S", "comp", "-o", "/dev/null", f.name],
                           capture_output=True, text=True)
        os.unlink(f.name)
        ok = r.returncode == 0
        bad += not ok
        print(("OK   " if ok else "FAIL ") + res)
        if not ok:
            print("\n".join(l for l in (r.stdout + r.stderr).splitlines() if "ERROR" in l)[:2000])
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

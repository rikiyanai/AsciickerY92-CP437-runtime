"""Launch admission for the True FF manual and headed launchers (FL-4516).

scripts/launch_fl4359_manual_play.py and scripts/run_fl4359_headed_movement.py
call admit() before Godot starts. A launch is refused when:

1. Any offline True FF launch-readiness test fails. This is the non-Metal
   scripts/tests/test_true_ff_*.py surface minus explicitly non-admission
   checks: Metal compilation/reflection are owned by the following gates, and
   registry-wide product/acceptance completeness remains tracked without
   blocking compilation.
2. The host/shader ABI gate fails. It reflects every TrueFFFeaturePasses
   program and compares descriptor bindings/types, push-constant sizes and
   shared constants before any Metal compile.
3. The Metal shader gate (scripts/fl4895_glsl_check.py --metal, goal-v4
   PROCESS) fails for any program owned by the True FF runtime. Successful
   results are keyed by selected shader/host source, checker and toolchain
   identity; structural withdrawal variants are checked.
3b. The offline whole-frame run fails (scripts/true_ff_offline_frame.py,
   FL-4877): the production P0 G-buffer/light map, every P1-P4 pass in host
   order, P5 layout and P6 RGBA16F glyph paint execute on the Metal GPU
   without Godot. Producer and layout diagnostics retain their surviving
   sites/rows. A terminal capacity
   overflow, missing current-frame shadow plan, or zero placed glyphs refuses
   the launch. Passing results are cached by the exact offline-frame source,
   data and toolchain inputs; unrelated gameplay/server edits do not evict
   them. This applies to agent and operator launches alike.
4. The runtime source is unchanged since the previous launch and that launch
   did not pass. A launch passes only when BOTH hold:
   - its godot_client.log carries no True FF rejection, script error or crash;
   - its canonical crawl proof exists: scripts/measure_true_ff_text_flips.py
     (registry gate.true_ff_text_flips, FL-4896/FL-4894) measured at least two
     frames with positive ink and adjacent pairs, zero RED/YELLOW/invalid frames;
   - a source-stable completion records a known zero engine exit.
   A missing crawl measurement is a failed launch, never an unknown one.
5. A blocking gap in config/true_ff_launch_gaps.json is open. This applies to
   every launch whose process ancestry contains Codex or Claude; only a manual
   session the operator starts outside an agent prints the gaps and proceeds. A gap stays
   open while any of its open_if source checks holds (defect pattern present,
   or required fix pattern absent). Fixing the code closes it; no log entry
   or status field does. A missing or malformed registry refuses every launch.

Every refusal prints the open gaps, which are the remaining conditions
before a headed run is justified.

A deliberate replicate of an unchanged failed launch passes --replicate-reason.
The reason is written to the launch ledger. Only the wired launchers are
covered; this module is not a security boundary for arbitrary programs.
"""
from __future__ import annotations

import hashlib
import fcntl
import importlib.metadata
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUARD_DIR = ROOT / ".run" / "true_ff_launch_guard"
LEDGER = GUARD_DIR / "launches.jsonl"
METAL_CACHE_DIR = GUARD_DIR / "metal_gate"
METAL_GATE = ROOT / "scripts" / "fl4895_glsl_check.py"
METAL_BACKEND = ROOT / "scripts" / "metal_backend_check.swift"
HOST_SHADER_CONTRACT = ROOT / "scripts" / "check_true_ff_host_shader_contract.py"
FEATURE_BANK_COMPILER = ROOT / "scripts" / "true_ff_feature_bank.py"
OFFLINE_FRAME = ROOT / "scripts" / "true_ff_offline_frame.py"
OFFLINE_FRAME_SOURCES = tuple(ROOT / "scripts" / name for name in (
    "true_ff_offline_frame.py", "true_ff_offline_world.py", "true_ff_offline_shaders.py",
    "true_ff_offline_metal.swift", "fl4895_glsl_check.py"))
OFFLINE_FRAME_CACHE = GUARD_DIR / "offline_frame" / "verdicts"
OFFLINE_FRAME_FINGERPRINT_SCHEMA = "true_ff.offline_frame_inputs.v1"
OFFLINE_TEST_CACHE = GUARD_DIR / "offline_tests"
OFFLINE_TEST_FINGERPRINT_SCHEMA = "true_ff.offline_test_inputs.v1"
OFFLINE_TEST_REPO_FILES = tuple(ROOT / name for name in (
    "pyproject.toml", "requirements.txt", "requirements-dev.txt",
))
# Inputs read by true_ff_offline_frame.py/true_ff_offline_world.py. Keep this
# ownership list explicit: using the full runtime fingerprint made an unrelated
# gameplay, UI or server edit repay the complete offline Metal frame.
OFFLINE_FRAME_PROJECT_FILES = tuple(Path(name) for name in (
    "render/ascii/features/TrueFFFeaturePasses.gd",
    "render/ascii/features/TrueFFFeatureABI.gd",
    "render/ascii/features/TrueFFFeatureBank.gd",
    "render/ascii/features/TrueFFFeatureRenderer.gd",
    "render/ascii/features/TrueFFVegetationFeatures.gd",
    "render/ascii/features/TrueFFWaterFeatures.gd",
    "render/ascii/features/TrueFFSkyFeatures.gd",
    "render/ascii/composition/TrueFFPageBackground.gd",
    "render/ascii/structure/TrueFFSceneGBuffer.gd",
    "render/ascii/structure/TrueFFStaticVisibility.gd",
    "render/ascii/structure/TrueFFTerrainVisibility.gd",
    "render/ascii/structure/TrueFFStructureABI.gd",
    "render/core/contracts/visibility/VisibleSurfaceABI.gd",
    "render/effects/water/WaterfallSiteField.gd",
    "render/scene/geometry/terrain/TerrainWaterGeometryStore.gd",
    "scripts/map/A3DMapLoader.gd",
    "scripts/map/TerrainMaterialPalette.gd",
    "assets/maps/game_map_y8.a3d",
    "assets/glyphs/true_ff/figure_part_timing.v1.json",
    "assets/glyphs/true_ff/saitamaar/catalog.json",
    "assets/glyphs/true_ff/saitamaar/atlas.png",
    "assets/glyphs/true_ff/outline_language.v1.json",
))
OFFLINE_FRAME_PROJECT_DIRS = (
    Path("assets/world_partition/true_ff_feature_bank"),
)
OFFLINE_FRAME_REPO_FILES = OFFLINE_FRAME_SOURCES + tuple(ROOT / name for name in (
    "config/godot_proof_pose_registry.json",
    "scripts/check_true_ff_host_shader_contract.py",
    "scripts/true_ff_feature_bank.py",
    "scripts/true_ff_terrain_sources.py",
    "scripts/compile_fl4789_chunk_catalog.py",
))
FEATURE_BANK_TERRAIN_MAP = ROOT / "godot_project" / "assets" / "maps" / "game_map_y8.a3d"
TRUE_FF_TESTS = tuple(sorted((ROOT / "scripts" / "tests").glob("test_true_ff_*.py")))
OFFLINE_TEST_EXCLUSIONS = (
    # Dedicated gates below own these compiler/reflection surfaces.
    "test_every_render_ascii_shader_stage_compiles_to_metal",
    "test_host_push_constants_match_reflected_sizes",
    "test_all_shader_stages_compile_for_metal",
    # This starts the Godot executable in headless script-load mode. It is a
    # useful pre-launch engine gate, but it is not an offline source/data test.
    "test_ff_frame_drivers_load_without_parse_or_compile_errors",
    # This validates all 237 product mappings, including visual acceptance and
    # Colyseus/gameplay ownership gaps. Those remain real registry failures,
    # but they are not evidence that shader compilation is unready.
    "test_live_mapping_population_and_water_cards_are_required",
)
GAPS = ROOT / "config" / "true_ff_launch_gaps.json"
GAPS_SCHEMA = "true_ff.launch_gaps.v2"
MANUAL_LAUNCHER = "launch_fl4359_manual_play.py"
GODOT_BINARY = Path(os.environ.get("GODOT_BIN") or "/Applications/Godot.app/Contents/MacOS/Godot")
REFUSED = 9

# Measurement/launcher edits do not constitute a change to the game.
FINGERPRINT_SCHEMA = "true_ff.runtime_tree.v2"
RUNTIME_LAUNCH_FILES = (
    ROOT / "scripts" / "true_ff_launch_guard.py",
    ROOT / "scripts" / "run_fl4359_headed_movement.py",
    ROOT / "scripts" / "launch_fl4359_manual_play.py",
    ROOT / "scripts" / "adhoc" / "fl4359_external_movement_driver.gd",
)
TREE_EXCLUDES = {".git", ".godot", "node_modules", "__pycache__", ".run"}
SHADER_SUFFIXES = (".glsl", ".glslinc", ".gdshader", ".gdshaderinc")
# GDScript only belongs in the Metal verdict when it changes a value consumed
# by the Metal checker.  Hashing every .gd file made an unrelated gameplay or
# UI edit evict the complete gate even though the expanded shader variants
# were byte-identical. These are the only runtime owners that name programs;
# FeaturePasses also owns TFF_TRAVERSE_STAGE specialization values.
METAL_PROGRAM_OWNERS = tuple(Path(name) for name in (
    "render/ascii/features/TrueFFFeaturePasses.gd",
    "render/ascii/features/TrueFFFeatureRenderer.gd",
    "render/ascii/structure/TrueFFSceneGBuffer.gd",
    "render/ascii/structure/TrueFFTerrainVisibility.gd",
    "render/ascii/structure/TrueFFStaticVisibility.gd",
    "render/ascii/composition/TrueFFPageBackground.gd",
))
METAL_SHADER_HOST_FILES = METAL_PROGRAM_OWNERS
LOG_FAILURE = re.compile(
    r"True FF frame rejected|true_ff_[a-z0-9_]*rejected|SCRIPT ERROR|Parse Error"
    r"|Program crashed|Segmentation fault|handle_crash"
    r"|TrueFFFrameRenderer: preparation:|residency preparation failed|timeout waiting for fence"
    # Engine pipeline/compile failures (FL-4516 audit: Metal backend internal
    # errors passed the offline gate and must fail the launch record).
    r"|error creating pipeline|Compiler encountered an internal error"
    r"|Condition \"!pipeline|pipeline creation failed|pipeline_rejected"
    r"|GPU Hang|command buffer[^\n]*(?:error|abort)|PixelCamera capture image normalization failed"
)
CRAWL_GATE = "gate.true_ff_text_flips"


def _tree_files(root: Path):
    if not root.is_dir():
        raise ValueError(f"runtime directory missing: {root}")
    for parent, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in TREE_EXCLUDES)
        for directory in dirs:
            if (Path(parent) / directory).is_symlink():
                raise ValueError(f"unresolved runtime directory symlink: {Path(parent) / directory}")
        for name in sorted(names):
            if name != ".DS_Store" and not name.endswith(".pyc"):
                yield Path(parent) / name


def _content_hash(path: Path) -> bytes:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").digest()


def runtime_fingerprint(project_dir: Path | None = None, server_dir: Path | None = None) -> str:
    """Hash the selected runtime roots, including non-Git replay trees.

    Generated caches/dependencies are not source. Ignored authored resources
    are included. Relative role names make identical relocated trees equal.
    """
    digest = hashlib.sha256(FINGERPRINT_SCHEMA.encode())
    for label, root in (("project", project_dir or ROOT / "godot_project"),
                        ("server", server_dir or ROOT / "colyseus_server")):
        for path in _tree_files(root):
            digest.update(f"{label}/{path.relative_to(root)}\0".encode())
            digest.update(_content_hash(path))
    # The launch path is executable runtime policy, not instrumentation.  It
    # chooses the scene, arguments, timeout and capture choreography; changing
    # it can turn an otherwise identical project tree from an impossible cold
    # launch into a valid one.  Omitting these files made the unchanged-source
    # rule reject the corrected launcher after its own 180-second watchdog had
    # killed Godot's first-frame Metal compilation.
    for path in RUNTIME_LAUNCH_FILES:
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"runtime launch source missing or symlinked: {path}")
        try:
            role = path.relative_to(ROOT)
        except ValueError:
            # Tests inject an isolated launcher outside the checkout.  The
            # bytes still own the fingerprint; only the stable role label is
            # reduced to its filename for that fixture seam.
            role = Path(path.name)
        digest.update(f"launch/{role}\0".encode())
        digest.update(_content_hash(path))
    return digest.hexdigest()


def _shader_file_hashes(project_dir: Path) -> dict[str, bytes]:
    """Return only inputs that can change the Metal gate's verdict.

    Shader sources include roots and includes.  The explicit host allowlist is
    kept beside the gate so a new host-fed specialization cannot silently
    become cache-invisible: its compiler implementation and regression test
    must be changed together.
    """
    shader_root = project_dir / "render" / "ascii"
    selected = {
        str(file.relative_to(project_dir)): _content_hash(file)
        for file in _tree_files(shader_root)
        if file.suffix in SHADER_SUFFIXES
    }
    for relative in METAL_SHADER_HOST_FILES:
        file = project_dir / relative
        if not file.is_file() or file.is_symlink():
            raise ValueError(f"Metal shader host source missing or symlinked: {file}")
        selected[str(relative)] = _content_hash(file)
    return selected


def _true_ff_shader_inventory(project_dir: Path) -> list[str]:
    """Exact production programs compiled by the True FF runtime owners."""
    inventory: set[str] = set()
    for relative in METAL_PROGRAM_OWNERS:
        owner = project_dir / relative
        if not owner.is_file() or owner.is_symlink():
            raise ValueError(f"True FF shader owner missing or symlinked: {owner}")
        inventory.update(re.findall(r'"res://(render/ascii/[^"\s]+\.glsl)"',
                                    owner.read_text(encoding="utf-8")))
    if not inventory:
        raise ValueError("True FF shader inventory is empty")
    for relative in inventory:
        shader = project_dir / relative
        if not shader.is_file() or shader.is_symlink():
            raise ValueError(f"True FF shader missing or symlinked: {shader}")
    return sorted(inventory)


def _changed_shader_files(project_dir: Path, before: dict[str, bytes] | None) -> list[str]:
    """Diagnostic only: which selected shader/host files differ from `before`."""
    try:
        after = _shader_file_hashes(project_dir)
    except (OSError, ValueError):
        return []
    if before is None:
        return []
    return sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))


def shader_fingerprint(project_dir: Path) -> str:
    digest = hashlib.sha256(METAL_GATE.read_bytes())
    digest.update(METAL_BACKEND.read_bytes())
    for name, content in _shader_file_hashes(project_dir).items():
        digest.update(name.encode() + b"\0" + content)
    for command in (["xcrun", "--find", "metal"], ["xcrun", "--show-sdk-path"],
                    ["xcrun", "-sdk", "macosx", "metal", "--version"]):
        result = subprocess.run(command, capture_output=True, check=True)
        digest.update(result.stdout)
    for name in ("glslangValidator", "spirv-cross", "xcrun"):
        executable = shutil.which(name)
        if not executable:
            raise ValueError(f"Metal gate tool missing: {name}")
        digest.update(executable.encode() + _content_hash(Path(executable)))
    # The engine's own shader/pipeline compiler is part of the identity: an
    # engine upgrade must not reuse a pass recorded against another binary.
    engine = GODOT_BINARY.resolve()
    if not engine.is_file():
        raise ValueError(f"Godot binary missing: {GODOT_BINARY}")
    digest.update(str(engine).encode() + _content_hash(engine))
    return digest.hexdigest()


def _offline_frame_toolchain_identity() -> bytes:
    """Identity of executables/interpreters that can change offline-frame bytes."""
    digest = hashlib.sha256(b"true_ff.offline_frame_toolchain.v1")
    for name in ("glslangValidator", "spirv-cross", "xcrun"):
        executable = shutil.which(name)
        if not executable:
            raise ValueError(f"offline frame tool missing: {name}")
        path = Path(executable).resolve()
        digest.update(name.encode() + b"\0" + str(path).encode() + b"\0" + _content_hash(path))
    for command in (("xcrun", "-sdk", "macosx", "metal", "--version"),
                    ("xcrun", "swiftc", "--version")):
        result = subprocess.run(command, capture_output=True, check=True)
        digest.update(b"\0".join(part.encode() for part in command) + b"\0")
        digest.update(result.stdout + result.stderr)
    digest.update(sys.version.encode())
    try:
        digest.update(importlib.metadata.version("Pillow").encode())
    except importlib.metadata.PackageNotFoundError as exc:
        raise ValueError("offline frame dependency missing: Pillow") from exc
    return digest.digest()


def offline_frame_fingerprint(project_dir: Path) -> str:
    """Hash only source, data and toolchain inputs consumed by the offline frame."""
    digest = hashlib.sha256(OFFLINE_FRAME_FINGERPRINT_SCHEMA.encode())
    digest.update(_offline_frame_toolchain_identity())
    for relative, content in sorted(_shader_file_hashes(project_dir).items()):
        digest.update(f"shader/{relative}\0".encode() + content)
    for relative in OFFLINE_FRAME_PROJECT_FILES:
        path = project_dir / relative
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"offline frame project input missing or symlinked: {path}")
        digest.update(f"project/{relative}\0".encode() + _content_hash(path))
    for relative in OFFLINE_FRAME_PROJECT_DIRS:
        root = project_dir / relative
        for path in _tree_files(root):
            digest.update(f"project/{relative}/{path.relative_to(root)}\0".encode())
            digest.update(_content_hash(path))
    for path in OFFLINE_FRAME_REPO_FILES:
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"offline frame repository input missing or symlinked: {path}")
        try:
            role = path.relative_to(ROOT)
        except ValueError:
            # Unit fixtures can inject an isolated owner; its bytes remain the
            # authority and the filename is the stable role inside that seam.
            role = Path(path.name)
        digest.update(f"repository/{role}\0".encode() + _content_hash(path))
    return digest.hexdigest()


def offline_test_fingerprint(project_dir: Path) -> str:
    """Exact source/tool identity for the offline pytest admission surface."""
    if project_dir.resolve() != (ROOT / "godot_project").resolve():
        raise ValueError("offline test fingerprint is bound to this checkout")
    digest = hashlib.sha256(OFFLINE_TEST_FINGERPRINT_SCHEMA.encode())
    for label, root in (("project", project_dir), ("scripts", ROOT / "scripts"),
                        ("config", ROOT / "config")):
        for path in _tree_files(root):
            digest.update(f"{label}/{path.relative_to(root)}\0".encode())
            digest.update(_content_hash(path))
    for path in OFFLINE_TEST_REPO_FILES:
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"offline test repository input missing or symlinked: {path}")
        digest.update(f"repository/{path.relative_to(ROOT)}\0".encode())
        digest.update(_content_hash(path))
    digest.update(sys.version.encode())
    digest.update(sys.executable.encode())
    for package in ("pytest", "pillow"):
        try:
            version = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            version = "missing"
        digest.update(f"{package}={version}\0".encode())
    compiler = shutil.which("c++") or shutil.which("clang++")
    if compiler:
        compiler_path = Path(compiler).resolve()
        digest.update(f"c++={compiler_path}\0".encode())
        if compiler_path.is_file():
            digest.update(_content_hash(compiler_path))
    else:
        digest.update(b"c++=missing\0")
    digest.update("\0".join(OFFLINE_TEST_EXCLUSIONS).encode())
    return digest.hexdigest()


def metal_gate(project_dir: Path) -> tuple[bool, str]:
    """Run, or reuse the cached result of, the Metal gate for this shader state."""
    try:
        before: dict[str, bytes] | None = _shader_file_hashes(project_dir)
    except (OSError, ValueError):
        before = None
    key = shader_fingerprint(project_dir)
    cache = METAL_CACHE_DIR / f"{key}.json"
    if cache.is_file():
        try:
            record = json.loads(cache.read_text(encoding="utf-8"))
            if record.get("rc") == 0 and record.get("project") == str(project_dir):
                return True, f"cached rc=0 {cache.name}"
        except (OSError, ValueError, KeyError, TypeError):
            pass
    inventory = _true_ff_shader_inventory(project_dir)
    print(f"[true-ff-launch-guard] running Metal gate ({len(inventory)} True FF programs)",
          flush=True)
    started = time.time()
    proc = subprocess.run([sys.executable, str(METAL_GATE), "--project", str(project_dir),
                           "--metal", *inventory], cwd=ROOT,
                          capture_output=True, text=True)
    reports = [line.removeprefix("METAL_GATE_RESULT ") for line in proc.stdout.splitlines()
               if line.startswith("METAL_GATE_RESULT ")]
    try:
        report = json.loads(reports[-1])
        valid = (report["project"] == str(project_dir) and report["shaders"] > 0
                 and report["variants"] >= report["shaders"] and report["failed"] == 0)
    except (IndexError, ValueError, KeyError, TypeError):
        valid = False
    if shader_fingerprint(project_dir) != key:
        changed = _changed_shader_files(project_dir, before)
        listed = "\n".join(f"  changed during gate: {name}" for name in changed[:20])
        return False, ("shader/toolchain source changed during Metal gate; the result cannot "
                       f"certify the source Godot would load\n{listed or '  toolchain identity changed'}")
    failing = [line for line in proc.stdout.splitlines() if line and not line.startswith("OK")]
    METAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({
        "rc": proc.returncode if valid else REFUSED, "project": str(project_dir),
        "shader_fingerprint": key, "seconds": round(time.time() - started, 1),
        "failing_lines": failing[-40:], "stderr_tail": proc.stderr[-4000:],
    }, indent=1) + "\n", encoding="utf-8")
    detail = "\n".join(failing[-12:] + proc.stderr.splitlines()[-8:])
    return proc.returncode == 0 and valid, f"rc={proc.returncode} inventory_valid={valid}\n{detail}".rstrip()


def offline_test_gate(project_dir: Path) -> tuple[bool, str]:
    """Run or reuse pass-only offline compile/data-contract readiness tests."""
    if project_dir.resolve() != (ROOT / "godot_project").resolve():
        return False, ("offline True FF tests are bound to this checkout's godot_project; "
                       f"selected tree is {project_dir}")
    if not TRUE_FF_TESTS:
        return False, "offline True FF test inventory is empty"
    key = offline_test_fingerprint(project_dir)
    cache = OFFLINE_TEST_CACHE / f"{key}.json"
    if cache.is_file():
        try:
            record = json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            record = {}
        if (record.get("schema") == OFFLINE_TEST_FINGERPRINT_SCHEMA
                and record.get("fingerprint") == key and record.get("ok") is True):
            return True, (f"cached rc=0 files={record.get('files')} "
                          f"nodes={record.get('nodes')} {cache.name}")
    selection = " and ".join(f"not {name}" for name in OFFLINE_TEST_EXCLUSIONS)
    command = [sys.executable, "-m", "pytest", "-q", *map(str, TRUE_FF_TESTS),
               "-k", selection]
    # A file-level allowlist is not a stage boundary: pytest can collect a
    # backend test imported into another selected module. Inspect the exact
    # selected node IDs before executing them so an exclusion cannot silently
    # become a Metal/Godot operation.
    # Pytest 9 prints a human tree for one `-q`; two quiet flags emit stable
    # `path::test_name` node IDs. The audit consumes those IDs, not console
    # formatting, so imported backend/runtime tests cannot hide in the tree.
    collected = subprocess.run([*command, "-q", "--collect-only"], cwd=ROOT,
                               capture_output=True, text=True)
    node_ids = [line.strip() for line in collected.stdout.splitlines()
                if "::test_" in line]
    leaked = [node for node in node_ids
              if any(f"::{name}" in node for name in OFFLINE_TEST_EXCLUSIONS)]
    if collected.returncode != 0 or not node_ids or leaked:
        detail = "\n".join((collected.stdout + "\n" + collected.stderr).splitlines()[-30:])
        return False, (f"offline test collection refused rc={collected.returncode} "
                       f"nodes={len(node_ids)} excluded_nodes={leaked[:8]}\n{detail}").rstrip()
    proc = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    detail = "\n".join((proc.stdout + "\n" + proc.stderr).splitlines()[-30:])
    if proc.returncode == 0:
        stable_key = offline_test_fingerprint(project_dir)
        if stable_key != key:
            return False, "offline test inputs changed while the suite ran"
        OFFLINE_TEST_CACHE.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({
            "schema": OFFLINE_TEST_FINGERPRINT_SCHEMA,
            "fingerprint": key,
            "ok": True,
            "files": len(TRUE_FF_TESTS),
            "nodes": len(node_ids),
        }, sort_keys=True) + "\n", encoding="utf-8")
    return proc.returncode == 0, (f"rc={proc.returncode} files={len(TRUE_FF_TESTS)} "
                                  f"nodes={len(node_ids)}\n{detail}").rstrip()


def feature_bank_gate(project_dir: Path) -> tuple[bool, str]:
    """Require the complete generated static+rigid+terrain inventory on disk."""
    if project_dir.resolve() != (ROOT / "godot_project").resolve():
        return False, ("feature-bank check is bound to this checkout's godot_project; "
                       f"selected tree is {project_dir}")
    proc = subprocess.run(
        [sys.executable, str(FEATURE_BANK_COMPILER), "build", "--all", "--rigid",
         "--terrain-map", str(FEATURE_BANK_TERRAIN_MAP), "--check"],
        cwd=ROOT, capture_output=True, text=True)
    detail = "\n".join((proc.stdout + "\n" + proc.stderr).splitlines()[-30:])
    return proc.returncode == 0, f"rc={proc.returncode}\n{detail}".rstrip()


def host_shader_contract_gate(project_dir: Path) -> tuple[bool, str]:
    """Compare the host ABI with reflected shader declarations before Metal."""
    proc = subprocess.run(
        [sys.executable, str(HOST_SHADER_CONTRACT), "--project", str(project_dir)],
        cwd=ROOT, capture_output=True, text=True)
    reports = [line.removeprefix("HOST_SHADER_CONTRACT_RESULT ")
               for line in proc.stdout.splitlines()
               if line.startswith("HOST_SHADER_CONTRACT_RESULT ")]
    try:
        report = json.loads(reports[-1])
        valid = (report["project"] == str(project_dir) and report["programs"] > 0
                 and report["variants"] >= report["programs"] and report["failed"] == 0)
    except (IndexError, ValueError, KeyError, TypeError):
        valid = False
    detail = "\n".join((proc.stdout + "\n" + proc.stderr).splitlines()[-30:])
    return proc.returncode == 0 and valid, \
        f"rc={proc.returncode} inventory_valid={valid}\n{detail}".rstrip()


def offline_frame_gate(project_dir: Path) -> tuple[bool, str]:
    """Run the whole True FF frame on Metal without Godot; refuse a rejected or blank frame."""
    if project_dir.resolve() != (ROOT / "godot_project").resolve():
        return False, ("offline frame is bound to this checkout's godot_project; "
                       f"selected tree is {project_dir}")
    cache = OFFLINE_FRAME_CACHE / f"{offline_frame_fingerprint(project_dir)}.json"
    if cache.is_file():
        try:
            record = json.loads(cache.read_text(encoding="utf-8"))
            if record.get("ok") is True and int(record.get("painted_pixels", 0)) > 0 \
                    and int(record.get("paper_pixels", 0)) > 0:
                return True, (f"cached ok glyphs={record.get('glyphs')} "
                              f"painted_pixels={record.get('painted_pixels')} "
                              f"paper_pixels={record.get('paper_pixels')} {cache.name}")
        except (OSError, ValueError):
            pass
    print("[true-ff-launch-guard] running offline whole frame (scripts/true_ff_offline_frame.py)",
          flush=True)
    proc = subprocess.run([sys.executable, str(OFFLINE_FRAME), "--json"], cwd=ROOT,
                          capture_output=True, text=True)
    reports = [line.removeprefix("TRUE_FF_OFFLINE_FRAME_RESULT ") for line in proc.stdout.splitlines()
               if line.startswith("TRUE_FF_OFFLINE_FRAME_RESULT ")]
    try:
        result = json.loads(reports[-1])
    except (IndexError, ValueError):
        result = {"ok": False}
    ok = proc.returncode == 0 and result.get("ok") is True
    if ok:
        OFFLINE_FRAME_CACHE.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
    failing = [line for line in proc.stdout.splitlines() if line.startswith("TRUE_FF_OFFLINE_FRAME FAIL")]
    detail = "\n".join(failing[-12:] + proc.stderr.splitlines()[-8:])
    return ok, (f"rc={proc.returncode} sites={result.get('sites')} glyphs={result.get('glyphs')} "
                f"painted_pixels={result.get('painted_pixels')} "
                f"paper_pixels={result.get('paper_pixels')} "
                f"work={result.get('work')}\n{detail}").rstrip()


def _crawl_ok(summary: dict) -> tuple[bool, str]:
    if not isinstance(summary, dict) or summary.get("schema") != "true_ff.text_flip_summary.v1":
        return False, "missing or unsupported crawl summary schema"
    for name in ("frames", "pairs", "red", "yellow", "invalid_frames"):
        if type(summary.get(name)) is not int or summary[name] < 0:
            return False, f"missing or invalid crawl counter: {name}"
    ink = summary.get("ink_chars_mean")
    if type(ink) not in (int, float) or not math.isfinite(ink) or ink <= 0:
        return False, "crawl has no measured ink"
    frames, pairs = summary["frames"], summary["pairs"]
    red, yellow, invalid = (summary[name] for name in ("red", "yellow", "invalid_frames"))
    text = f"frames={frames} red={red} yellow={yellow} invalid={invalid}"
    return frames >= 2 and 0 < pairs < frames and red == yellow == invalid == 0, text + f" pairs={pairs} ink={ink}"


def _headed_crawl(run_dir: Path) -> list[tuple[bool, str]]:
    summary_path = run_dir / "analysis_summary.json"
    if not summary_path.is_file():
        return []
    try:
        document = json.loads(summary_path.read_text(encoding="utf-8"))
        flips = document.get("true_ff_text_flips")
    except (OSError, ValueError, AttributeError):
        return [(False, "analysis_summary.json unreadable")]
    if not isinstance(flips, dict):
        return []
    if not isinstance(flips.get("f9_windows", []), list):
        return [(False, "invalid f9_windows")]
    results: list[tuple[bool, str]] = []
    # A missing run export is a failure of this run; an F9 bundle elsewhere
    # must not stand in for the session's own measurement.
    if not flips.get("run") and flips.get("rc") not in (None, 0):
        results.append((False, f"run crawl rc={flips.get('rc')} {flips.get('reason', '')}"))
    for label, row in [("run", flips.get("run") or {}),
                       *[("f9", w) for w in flips.get("f9_windows") or []]]:
        if not isinstance(row, dict) or not row:
            continue
        if type(row.get("rc")) is not int or row["rc"] != 0:
            results.append((False, f"{label} crawl rc={row.get('rc')} {row.get('reason', '')}"))
            continue
        ok, text = _crawl_ok(row.get("summary") or {})
        results.append((ok, f"{label} crawl {text}"))
    return results


def _bundle_roots(run_dir: Path) -> list[Path]:
    roots = [run_dir]
    override = os.environ.get("ASCIICKER_SCREEN_RECORDING_DIR", "").strip()
    if override:
        roots.append(Path(override))
    roots.append(Path.home() / "Desktop")
    return [r for r in dict.fromkeys(roots) if r.is_dir()]


def _bundle_crawl(run_dir: Path, since: float) -> list[tuple[bool, str]]:
    """F9 bundles whose launch identity names this run directory."""
    results: list[tuple[bool, str]] = []
    target = run_dir.resolve()
    for root in _bundle_roots(run_dir):
        for bundle in root.iterdir():
            completion = bundle / "completion.json"
            manifest = bundle / "manifest.json"
            if not completion.is_file() or not manifest.is_file() or bundle.stat().st_mtime < since:
                continue
            try:
                source = json.loads(manifest.read_text(encoding="utf-8"))["identity"]["source"]
                receipt = str(source["run_identity"]["route_receipt"])
            except (OSError, ValueError, KeyError, TypeError):
                continue
            receipt_path = Path(receipt) if Path(receipt).is_absolute() else ROOT / receipt
            if receipt_path.resolve().parent != target:
                continue
            try:
                document = json.loads(completion.read_text(encoding="utf-8"))
                crawl = document["outputs"]["crawl"]
                if document.get("status") != "complete":
                    results.append((False, f"{bundle.name}: recording incomplete"))
                    continue
            except (OSError, ValueError, KeyError, TypeError):
                results.append((False, f"{bundle.name}: crawl output missing"))
                continue
            if not isinstance(crawl, dict):
                results.append((False, f"{bundle.name}: invalid crawl output"))
                continue
            if crawl.get("status") != "ok":
                results.append((False, f"{bundle.name}: crawl {crawl.get('status')} "
                                       f"{crawl.get('error', crawl.get('reason', ''))}"))
                continue
            ok, text = _crawl_ok(crawl.get("summary") or {})
            results.append((ok, f"{bundle.name}: crawl {text}"))
    return results


def _log_failures(log: Path, limit: int = 5) -> list[str]:
    found: list[str] = []
    with log.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if LOG_FAILURE.search(line):
                found.append(line.strip()[:200])
                if len(found) >= limit:
                    break
    return found


def launch_outcome(record: dict) -> tuple[bool, list[str]]:
    """(passed, reasons) for one ledger record."""
    run_dir = Path(str(record.get("run_dir", "")))
    log = run_dir / "godot_client.log"
    if not log.is_file():
        return False, [f"godot_client.log missing in {run_dir}"]
    reasons: list[str] = [f"log: {line}" for line in _log_failures(log)]
    try:
        completion = json.loads((run_dir / "guard_completion.json").read_text())
        if (completion.get("fingerprint") != record.get("fingerprint")
                or completion.get("source_stable") is not True
                or type(completion.get("godot_rc")) is not int
                or completion["godot_rc"] != 0):
            reasons.append("no clean, source-stable engine completion")
    except (OSError, ValueError, AttributeError):
        reasons.append("guard completion missing or unreadable (running/legacy/unknown is not passed)")
    crawl = _headed_crawl(run_dir) + _bundle_crawl(run_dir, float(record.get("time", 0.0)) - 5.0)
    if not crawl:
        reasons.append(f"no crawl proof ({CRAWL_GATE}): no measured True FF text frames for this run")
    reasons += [f"crawl: {text}" for ok, text in crawl if not ok]
    return not reasons, reasons


def _owner_path(name: str, project_dir: Path) -> Path:
    """Registry paths are repository-relative; godot_project/ follows the selected project."""
    prefix = "godot_project/"
    return project_dir / name[len(prefix):] if name.startswith(prefix) else ROOT / name


def open_gaps(project_dir: Path | None = None) -> list[dict]:
    """Open launch gaps, each with a 'status' line. Raises ValueError on a bad registry."""
    project_dir = (project_dir or ROOT / "godot_project").resolve()
    try:
        registry = json.loads(GAPS.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"launch gap registry unreadable: {GAPS}: {exc}") from exc
    if not isinstance(registry, dict) or registry.get("schema") != GAPS_SCHEMA \
            or not isinstance(registry.get("gaps"), list):
        raise ValueError(f"launch gap registry {GAPS} lacks schema {GAPS_SCHEMA} or a gaps list")
    found: list[dict] = []
    for gap in registry["gaps"]:
        checks = gap.get("open_if") if isinstance(gap, dict) else None
        if not isinstance(gap, dict) or not isinstance(gap.get("id"), str) \
                or type(gap.get("blocks_launch")) is not bool or not isinstance(checks, list) or not checks:
            raise ValueError(f"malformed launch gap entry in {GAPS}: {gap!r}"[:300])
        holding: list[str] = []
        files: list[str] = []
        for check in checks:
            kinds = [k for k in ("present", "absent") if k in check] if isinstance(check, dict) else []
            names = check.get("files") if len(kinds) == 1 else None
            if not isinstance(names, list) or not names or not all(isinstance(n, str) for n in names):
                raise ValueError(f"malformed open_if check in {gap['id']}: {check!r}"[:300])
            try:
                pattern = re.compile(check[kinds[0]])
            except re.error as exc:
                raise ValueError(f"bad open_if pattern in {gap['id']}: {exc}") from exc
            texts: list[str] = []
            for name in names:
                path = _owner_path(name, project_dir)
                if not path.is_file():
                    raise ValueError(f"gap {gap['id']} names a missing owner file: {name}")
                texts.append(path.read_text(encoding="utf-8", errors="replace"))
            files += names
            matched = any(pattern.search(text) for text in texts)
            if matched == (kinds[0] == "present"):
                holding.append(str(check.get("means") or f"{kinds[0]} {check[kinds[0]]}"))
        if holding:
            found.append({**gap, "status": "open: " + "; ".join(holding),
                          "owner_files": list(dict.fromkeys(files))})
    return found


def _print_gaps(tag: str, gaps: list[dict]) -> None:
    if not gaps:
        print(f"{tag} open launch gaps: none ({os.path.relpath(GAPS, ROOT)})", flush=True)
        return
    print(f"{tag} remaining gaps before a headed run is justified ({os.path.relpath(GAPS, ROOT)}):",
          flush=True)
    for gap in gaps:
        kind = "BLOCKING" if gap["blocks_launch"] else "open"
        print(f"{tag}   [{kind}] {gap['id']}: {gap.get('defect', '')}", flush=True)
        print(f"{tag}       requirement: {gap.get('requirement', '')}", flush=True)
        print(f"{tag}       {gap['status']}; owners: {', '.join(gap['owner_files'])}", flush=True)


def _print_gaps_safe(tag: str, project_dir: Path) -> None:
    """Every refusal names the remaining gaps; a broken registry is itself reported."""
    try:
        _print_gaps(tag, open_gaps(project_dir))
    except (OSError, ValueError) as exc:
        print(f"{tag} launch gap registry unusable: {exc}", flush=True)


AGENT_PROCESS = re.compile(r"(?i)(^|/)(codex|claude)$|codexcli\.app|codex framework")


def agent_ancestor(pid: int | None = None) -> str:
    """The first agent process (Codex/Claude) above this launch, or '' for an operator shell.

    An unreadable process chain counts as an agent launch (fail closed).
    """
    pid = os.getpid() if pid is None else pid
    for _ in range(64):
        result = subprocess.run(["ps", "-o", "ppid=", "-o", "comm=", "-p", str(pid)],
                                capture_output=True, text=True)
        fields = result.stdout.strip().split(None, 1)
        if result.returncode or len(fields) != 2 or not fields[0].isdigit():
            return f"unreadable process chain at pid {pid}"
        parent, command = int(fields[0]), fields[1].strip()
        if AGENT_PROCESS.search(command):
            return f"{command} (pid {pid})"
        if parent <= 1:
            return ""
        pid = parent
    return "process chain deeper than 64"


def evidence_failures(record: dict) -> list[str]:
    """Positive evidence that a launch failed: missing log, failure lines, nonzero exit.

    A manual session never carries a crawl proof, so absence of proof is not
    failure evidence for it; a headed proof is judged by launch_outcome.
    """
    run_dir = Path(str(record.get("run_dir", "")))
    log = run_dir / "godot_client.log"
    if not log.is_file():
        return [f"godot_client.log missing in {run_dir}"]
    reasons = [f"log: {line}" for line in _log_failures(log)]
    try:
        completion = json.loads((run_dir / "guard_completion.json").read_text())
        rc = completion.get("godot_rc")
        if type(rc) is int and rc != 0:
            reasons.append(f"engine exit {rc}")
        if completion.get("source_stable") is False:
            reasons.append("runtime source changed while the launch ran")
    except (OSError, ValueError, AttributeError):
        pass
    return reasons


def _records() -> list[dict]:
    """Every ledger record, oldest first. A corrupt ledger refuses."""
    if not LEDGER.is_file():
        return []
    records: list[dict] = []
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                records.append(json.loads(line))
            except ValueError as exc:
                raise ValueError("launch ledger is corrupt; preserve and repair it") from exc
    if any(not isinstance(row, dict) for row in records):
        raise ValueError("launch ledger contains a non-record")
    return records


def _last_record() -> dict | None:
    """Include pending admissions: an absent log must not unlock a second run."""
    if not LEDGER.is_file():
        return None
    records: list[dict] = []
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                records.append(json.loads(line))
            except ValueError as exc:
                raise ValueError("launch ledger is corrupt; preserve and repair it") from exc
    if any(not isinstance(row, dict) for row in records):
        raise ValueError("launch ledger contains a non-record")
    return records[-1] if records else None


def _admit(launcher: str, run_dir: Path, replicate_reason: str,
           project_dir: Path, server_dir: Path, presentation_mode: str) -> int:
    """0 admits the launch and records it; REFUSED prints why and starts nothing."""
    tag = "[true-ff-launch-guard]"
    try:
        if (run_dir / "guard_admission.json").exists() or (run_dir / "godot_client.log").exists():
            raise ValueError("run directory already contains launch evidence; use a fresh run directory")
        if not (project_dir / "project.godot").is_file() or not (server_dir / "package.json").is_file():
            raise ValueError("selected project/server root lacks project.godot or package.json")
        fingerprint = runtime_fingerprint(project_dir, server_dir)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"{tag} REFUSED: runtime source fingerprint failed: {error}", flush=True)
        _print_gaps_safe(tag, project_dir)
        return REFUSED
    mode = presentation_mode.strip().upper()
    if mode not in {"FF", "FC", "CF", "CC", "FF_SHARED"}:
        print(f"{tag} REFUSED: invalid presentation mode {presentation_mode!r}", flush=True)
        return REFUSED

    # FL-4880: the admission token is an engine-launch boundary, but the banks,
    # Metal programs, whole-frame run, temporal crawl, and gap registry below
    # are True-FF evidence. An explicitly selected non-FF mode must not be
    # blocked by unrelated FF products. FL-4907: FF_SHARED selects the shared
    # probe compositor with flat/flat bits, not standalone True FF. It still
    # receives a one-use token and the same source/root revalidation.
    if mode != "FF":
        if replicate_reason.strip():
            print(f"{tag} REFUSED: --replicate-reason applies only to FF launches", flush=True)
            return REFUSED
        if runtime_fingerprint(project_dir, server_dir) != fingerprint:
            print(f"{tag} REFUSED: runtime source changed during admission; recheck the new source",
                  flush=True)
            return REFUSED
        detail = f"not_applicable:presentation_mode={mode}"
        GUARD_DIR.mkdir(parents=True, exist_ok=True)
        record = {
            "time": time.time(), "launcher": launcher, "run_dir": str(run_dir),
            "fingerprint_schema": FINGERPRINT_SCHEMA, "fingerprint": fingerprint,
            "project_dir": str(project_dir), "server_dir": str(server_dir),
            "presentation_mode": mode, "replicate_reason": "",
            "feature_bank_gate": detail, "offline_test_gate": detail,
            "host_shader_contract_gate": detail, "offline_frame_gate": detail,
        }
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "guard_admission.json").write_text(json.dumps(record, sort_keys=True) + "\n")
        with LEDGER.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
        print(f"{tag} admitted shared compositor mode={mode}: fingerprint={fingerprint[:12]}", flush=True)
        return 0

    try:
        gaps = open_gaps(project_dir)
    except (OSError, ValueError) as error:
        print(f"{tag} REFUSED: {error}", flush=True)
        return REFUSED
    blocking = [gap["id"] for gap in gaps if gap["blocks_launch"]]
    # goal-v4 PROCESS: the operator launches the game. Open gaps gate every
    # agent launch; only a manual session started outside Codex/Claude
    # proceeds with them printed. The launcher name alone is not identity.
    agent = agent_ancestor() if blocking else ""
    if blocking and launcher.endswith(MANUAL_LAUNCHER) and not agent:
        _print_gaps(tag, gaps)
        blocking = []
    elif blocking and agent:
        print(f"{tag} launch started by agent process {agent}", flush=True)
    if blocking:
        print(f"{tag} REFUSED: {len(blocking)} blocking launch gap(s) open: {', '.join(blocking)}",
              flush=True)
        _print_gaps(tag, gaps)
        print(f"{tag} A gap closes when its owner source no longer meets its open_if check "
              f"in {os.path.relpath(GAPS, ROOT)}; no log entry is required.", flush=True)
        return REFUSED
    records = _records()
    replicate = replicate_reason.strip()
    if replicate:
        replicate_agent = agent_ancestor()
        if replicate_agent:
            print(f"{tag} REFUSED: --replicate-reason is an operator override; this launch "
                  f"comes from agent process {replicate_agent}.", flush=True)
            _print_gaps(tag, gaps)
            return REFUSED
    # Compare against the most recent launch of THIS source, not merely the
    # last record: an intervening launch of other source must not unlock it.
    same = next((r for r in reversed(records) if r.get("fingerprint_schema") == FINGERPRINT_SCHEMA
                 and r.get("fingerprint") == fingerprint), None)
    # A legacy-schema last record cannot establish a runtime-source change.
    if same is None and records and records[-1].get("fingerprint_schema") != FINGERPRINT_SCHEMA:
        same = records[-1]
    if same is not None and not replicate:
        if str(same.get("launcher", "")).endswith(MANUAL_LAUNCHER) \
                and same.get("fingerprint_schema") == FINGERPRINT_SCHEMA:
            reasons = evidence_failures(same)
            failed = bool(reasons)
        else:
            passed, reasons = launch_outcome(same)
            failed = not passed
        if failed:
            print(f"{tag} REFUSED: runtime source is unchanged since a launch that did not pass.",
                  flush=True)
            print(f"{tag} previous: {same.get('launcher')} {same.get('run_dir')}", flush=True)
            for reason in reasons:
                print(f"{tag}   - {reason}", flush=True)
            print(f"{tag} Change runtime source first. An operator replicate passes "
                  f"--replicate-reason \"<why>\".", flush=True)
            _print_gaps(tag, gaps)
            return REFUSED
    ok, bank_detail = feature_bank_gate(project_dir)
    if not ok:
        print(f"{tag} REFUSED: generated True FF banks differ ({bank_detail})", flush=True)
        _print_gaps(tag, gaps)
        return REFUSED
    ok, offline_detail = offline_test_gate(project_dir)
    if not ok:
        print(f"{tag} REFUSED: offline True FF tests failed ({offline_detail})", flush=True)
        _print_gaps(tag, gaps)
        return REFUSED
    ok, contract_detail = host_shader_contract_gate(project_dir)
    if not ok:
        print(f"{tag} REFUSED: host/shader contract failed ({contract_detail})", flush=True)
        _print_gaps(tag, gaps)
        return REFUSED
    ok, detail = metal_gate(project_dir)
    if not ok:
        print(f"{tag} REFUSED: Metal gate failed ({detail})", flush=True)
        _print_gaps(tag, gaps)
        return REFUSED
    try:
        ok, frame_detail = offline_frame_gate(project_dir)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        ok, frame_detail = False, f"offline frame fingerprint failed: {error}"
    if not ok:
        print(f"{tag} REFUSED: offline whole-frame run failed ({frame_detail})", flush=True)
        _print_gaps(tag, gaps)
        return REFUSED
    if runtime_fingerprint(project_dir, server_dir) != fingerprint:
        print(f"{tag} REFUSED: runtime source changed during admission; recheck the new source", flush=True)
        _print_gaps(tag, gaps)
        return REFUSED
    GUARD_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "time": time.time(), "launcher": launcher, "run_dir": str(run_dir),
        "fingerprint_schema": FINGERPRINT_SCHEMA, "fingerprint": fingerprint,
        "project_dir": str(project_dir), "server_dir": str(server_dir),
        "presentation_mode": mode,
        "replicate_reason": replicate_reason.strip(),
        "feature_bank_gate": bank_detail.splitlines()[0],
        "offline_test_gate": offline_detail.splitlines()[0],
        "host_shader_contract_gate": contract_detail.splitlines()[0],
        "offline_frame_gate": frame_detail.splitlines()[0],
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "guard_admission.json").write_text(json.dumps(record, sort_keys=True) + "\n")
    with LEDGER.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    print(f"{tag} admitted: fingerprint={fingerprint[:12]} "
          f"offline_tests={offline_detail.splitlines()[0]} "
          f"host_shader_contract={contract_detail.splitlines()[0]} "
          f"metal_gate={detail.splitlines()[0]}",
          flush=True)
    return 0


def admit(launcher: str, run_dir: Path, replicate_reason: str = "", *,
          project_dir: Path | None = None, server_dir: Path | None = None,
          presentation_mode: str = "FF") -> int:
    """Serialize admissions and fail closed on missing roots/tools/evidence."""
    try:
        GUARD_DIR.mkdir(parents=True, exist_ok=True)
        with (GUARD_DIR / "admission.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return _admit(launcher, run_dir.resolve(), replicate_reason,
                          (project_dir or ROOT / "godot_project").resolve(),
                          (server_dir or ROOT / "colyseus_server").resolve(),
                          presentation_mode)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"[true-ff-launch-guard] REFUSED: {exc}", flush=True)
        _print_gaps_safe("[true-ff-launch-guard]", (project_dir or ROOT / "godot_project").resolve())
        return REFUSED


def before_spawn(run_dir: Path, project_dir: Path, server_dir: Path) -> int:
    """Revalidate selected roots after launcher preparation, just before spawn.

    This detects edits across preparation; it is not an immutable filesystem
    snapshot. Completion rechecks source again and fails an unstable run.
    """
    try:
        record = json.loads((run_dir / "guard_admission.json").read_text())
        if (record["project_dir"] != str(project_dir.resolve())
                or record["server_dir"] != str(server_dir.resolve())
                or record["fingerprint"] != runtime_fingerprint(project_dir, server_dir)):
            raise ValueError("selected runtime source changed after admission")
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"[true-ff-launch-guard] REFUSED before spawn: {exc}", flush=True)
        _print_gaps_safe("[true-ff-launch-guard]", project_dir.resolve())
        return REFUSED


def record_completion(run_dir: Path, godot_rc: int | None) -> None:
    record = json.loads((run_dir / "guard_admission.json").read_text())
    try:
        stable = record["fingerprint"] == runtime_fingerprint(
            Path(record["project_dir"]), Path(record["server_dir"]))
    except (OSError, ValueError):
        stable = False
    (run_dir / "guard_completion.json").write_text(json.dumps({
        "fingerprint": record["fingerprint"], "godot_rc": godot_rc,
        "source_stable": stable, "time": time.time(),
    }, sort_keys=True) + "\n")


def main(argv: list[str]) -> int:
    """Read-only report: python3 scripts/true_ff_launch_guard.py status"""
    if argv[:1] != ["status"]:
        print(main.__doc__)
        return 2
    fingerprint = runtime_fingerprint()
    previous = _last_record()
    print(f"runtime fingerprint {fingerprint}")
    _print_gaps("[true-ff-launch-guard]", open_gaps())
    if previous is None:
        print("no previous launch recorded")
        return 0
    passed, reasons = launch_outcome(previous)
    same = previous.get("fingerprint") == fingerprint
    print(f"previous {previous.get('launcher')} {previous.get('run_dir')} "
          f"passed={passed} unchanged_source={same}")
    for reason in reasons:
        print(f"  - {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""Execute the real compiled image/metadata owner; no viewport acceptance claim."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import tempfile
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


@pytest.fixture(scope="module")
def compiled_program():
    spec = importlib.util.spec_from_file_location(
        "fl4512_authoring_program_test", ROOT / "scripts/compile_fl4683_glyph_program.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    store = ROOT / "assets/glyphs/generated/fl4512_authoring"
    store.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="transaction-test-", dir=store) as directory:
        staging = Path(directory)
        output = (staging / "bundle").relative_to(ROOT)
        pool = json.loads((ROOT / "assets/glyphs/generated/fl4512.compiled_pool.v1.json").read_text())
        pool["header"]["pool_revision"] += 1
        del pool["artifact_sha256"]
        pool["artifact_sha256"] = hashlib.sha256(json.dumps(
            pool, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()).hexdigest()
        candidate_pool = staging / "next-pool.json"
        candidate_pool.write_text(json.dumps(pool, sort_keys=True, separators=(",", ":")))
        receipt = module.compile_authoring_transaction(
            ROOT / "assets/glyphs/profiles/material_rendering_profiles.v1.json",
            candidate_pool, output,
        )
        yield receipt


@pytest.fixture(scope="module")
def program_probe(tmp_path_factory):
    output = tmp_path_factory.mktemp("fl4512_program_authoring")
    cjson = output / "cJSON.o"
    subprocess.run([
        "cc", "-ffunction-sections", "-fdata-sections", "-c",
        str(ROOT / "engine/third_party/cjson/cJSON.c"), "-o", str(cjson),
    ], check=True, cwd=ROOT, capture_output=True, text=True)
    binary = output / "probe"
    command = [
        "c++", "-std=c++20", "-O1", "-fsanitize=address,undefined",
        "-fno-omit-frame-pointer", "-ffunction-sections", "-fdata-sections",
        "-DFL4512_COMPILED_LOADER_MUTATION_PROBE=1",
        "-DFL4683_GLYPH_PROGRAM_LOADER_ENABLED", "-DFL4693_PRODUCT_GLYPH_PROGRAM_ENABLED",
    ]
    for include in (ROOT, ROOT / "engine", ROOT / "engine/render", ROOT / "server", ROOT / "platform"):
        command += ["-I", str(include)]
    command += [str(ROOT / name) for name in (
        "scripts/tests/fl4512_program_authoring_harness.cpp",
        "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp",
        "apps/fl4512-runtime/engine/fl4512_profile_workspace.cpp",
        "apps/fl4512-runtime/engine/fl4131_harri_resolver.cpp",
        "apps/fl4512-runtime/platform/fl4683_glyph_program_loader.cpp",
        "apps/fl4512-runtime/platform/fl4683_exact_cell_selector.cpp",
        "apps/fl4512-runtime/platform/fl4693_product_glyph_program.cpp",
    )]
    command += [str(cjson), "-Wl,-dead_strip", "-o", str(binary)]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return binary


def workspace_at(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / "profiles").mkdir(parents=True)
    (workspace / "generated").mkdir()
    shutil.copyfile(ROOT / "assets/glyphs/profiles/material_rendering_profiles.v1.json",
                    workspace / "profiles/material_rendering_profiles.v1.json")
    pool_source = ROOT / "assets/glyphs/generated/fl4512.compiled_pool.v1.json"
    pool_path = workspace / "generated/fl4512.compiled_pool.v1.json"
    shutil.copyfile(pool_source, pool_path)
    return workspace


def run_probe(binary, workspace, mode, receipt):
    result = subprocess.run([
        str(binary), str(workspace), str(ROOT / "assets/glyphs"), mode, str(receipt)
    ], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_detached_program_publish_and_stale_rejection(program_probe, compiled_program, tmp_path):
    workspace = workspace_at(tmp_path)
    observed = run_probe(program_probe, workspace, "publish", compiled_program)
    assert "authoring-program:detached+publish+stale-rejected+duplicate-rejected" in observed
    receipt = json.loads((ROOT / compiled_program).read_text())
    assert receipt["sidecar"]["sha256"] in observed


def test_restart_restores_matching_program_and_rejects_mixed_source(
    program_probe, compiled_program, tmp_path
):
    workspace = workspace_at(tmp_path)
    shutil.copyfile(ROOT / compiled_program.parent / "pool.json",
                    workspace / "generated/fl4512.compiled_pool.v1.json")
    shutil.copyfile(ROOT / compiled_program, workspace / "generated/fl4512.active_program.v1.json")
    observed = run_probe(program_probe, workspace, "restore", compiled_program)
    receipt = json.loads((ROOT / compiled_program).read_text())
    assert receipt["sidecar"]["sha256"] in observed
    profile = workspace / "profiles/material_rendering_profiles.v1.json"
    profile.write_bytes(profile.read_bytes() + b" ")
    run_probe(program_probe, workspace, "reject_restore", compiled_program)


def test_running_owner_follows_only_complete_forward_workspace_snapshots(
    program_probe, compiled_program, tmp_path
):
    """One PID survives missing/partial/corrupt snapshots and accepts repair."""
    workspace = workspace_at(tmp_path)
    active = workspace / "generated/fl4512.active_program.v1.json"
    pool = workspace / "generated/fl4512.compiled_pool.v1.json"
    profile = workspace / "profiles/material_rendering_profiles.v1.json"
    original_profile = profile.read_bytes()
    receipt_bytes = (ROOT / compiled_program).read_bytes()
    receipt = json.loads(receipt_bytes)
    process = subprocess.Popen([
        str(program_probe), str(workspace), str(ROOT / "assets/glyphs"),
        "follow", str(compiled_program),
    ], cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    pending = bytearray()

    def census():
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            while b"\n" in pending:
                line, _, rest = pending.partition(b"\n")
                pending[:] = rest
                if line.startswith(b"CENSUS "):
                    return json.loads(line[7:])
            ready, _, _ = select.select([process.stdout], [], [], max(0, deadline - time.monotonic()))
            assert ready, "owner did not return a frame-boundary census"
            data = os.read(process.stdout.fileno(), 65536)
            assert data, "owner exited before returning census"
            pending.extend(data)
        pytest.fail("owner census timeout")

    def frame(after_rejection=False):
        # Exercise the production debounce/retry clock, not a bypass hook.
        time.sleep(1.05 if after_rejection else .27)
        process.stdin.write(b"frame\n")
        process.stdin.flush()
        return census()

    def publish_receipt(data):
        staged = active.with_suffix(".staged")
        staged.write_bytes(data)
        staged.replace(active)

    try:
        before = census()
        assert before["program_ready"]
        missing = frame()
        assert missing["sidecar_sha256"] == before["sidecar_sha256"]
        # A receipt arriving before its pool is not a complete transaction.
        publish_receipt(receipt_bytes)
        partial = frame()
        assert partial["workspace_last_result"] == "workspace_program_pool_mismatch"
        assert partial["profile_revision"] == before["profile_revision"]
        pool.write_bytes((ROOT / compiled_program.parent / "pool.json").read_bytes())
        profile.write_bytes(original_profile + b" ")
        mixed = frame(after_rejection=True)
        assert mixed["workspace_last_result"] == "authoring_profile_pin_mismatch"
        assert mixed["sidecar_sha256"] == before["sidecar_sha256"]
        profile.write_bytes(original_profile)
        accepted = frame(after_rejection=True)
        assert accepted["workspace_updates"] == 1
        assert accepted["profile_revision"] == before["profile_revision"] + 1
        assert accepted["sidecar_sha256"] == receipt["sidecar"]["sha256"]
        assert accepted["workspace_receipt_sha256"] == hashlib.sha256(receipt_bytes).hexdigest()
        # Replacing the file with identical bytes must not republish it.
        publish_receipt(receipt_bytes)
        assert frame()["workspace_updates"] == 1
        publish_receipt(b"{broken")
        corrupt = frame()
        assert corrupt["workspace_rejected"] == 3
        assert corrupt["program_ready"]
        assert corrupt["sidecar_sha256"] == accepted["sidecar_sha256"]
        assert process.poll() is None
    finally:
        process.stdin.close()
        process.wait(timeout=10)
        diagnostics = process.stderr.read().decode()
        process.stdout.close()
        process.stderr.close()
    assert process.returncode == 0, diagnostics
    assert "runtime error:" not in diagnostics and "AddressSanitizer" not in diagnostics

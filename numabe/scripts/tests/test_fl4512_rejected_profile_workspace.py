import hashlib
import json
from pathlib import Path
import subprocess
import sys


REPO = Path(__file__).resolve().parents[2]
PRODUCER = REPO / "scripts/fl4548/fl4512_build_rejected_profile_workspace.py"
SOURCE_PROFILE = REPO / "assets/glyphs/profiles/material_rendering_profiles.v1.json"
SOURCE_POOL = REPO / "assets/glyphs/generated/fl4512.compiled_pool.v1.json"
PROFILE_RELATIVE = Path("profiles/material_rendering_profiles.v1.json")
POOL_RELATIVE = Path("generated/fl4512.compiled_pool.v1.json")


def run_producer(workspace: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(PRODUCER), "--workspace", str(workspace)],
        cwd=REPO,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_hash_without_self(value: dict) -> str:
    payload = dict(value)
    payload.pop("artifact_sha256")
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return sha256_bytes(encoded)


def test_rejected_workspace_producer_is_byte_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    assert run_producer(first).returncode == 0
    assert run_producer(second).returncode == 0
    assert (first / PROFILE_RELATIVE).read_bytes() == (second / PROFILE_RELATIVE).read_bytes()
    assert (first / POOL_RELATIVE).read_bytes() == (second / POOL_RELATIVE).read_bytes()
    first_manifest = json.loads((first / "workspace_manifest.json").read_text())
    second_manifest = json.loads((second / "workspace_manifest.json").read_text())
    assert first_manifest["semantic_workspace_sha256"] == second_manifest[
        "semantic_workspace_sha256"
    ]
    assert first_manifest["root_path"] != second_manifest["root_path"]


def test_corruption_changes_only_embedded_artifact_hash_nibble(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    assert run_producer(workspace).returncode == 0
    source = SOURCE_POOL.read_bytes()
    rejected = (workspace / POOL_RELATIVE).read_bytes()
    differences = [
        index
        for index, (before, after) in enumerate(zip(source, rejected, strict=True))
        if before != after
    ]
    assert len(differences) == 1
    manifest = json.loads((workspace / "workspace_manifest.json").read_text())
    assert differences[0] == manifest["changed_byte_offset"]
    assert sha256_bytes(rejected) == manifest["rejected_bytes_sha256"]


def test_rejected_fixture_is_parseable_and_declares_complete_domain(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    assert run_producer(workspace).returncode == 0
    rejected = json.loads((workspace / POOL_RELATIVE).read_text())
    keys = sorted(
        f"{material['profile_namespace']}:{material['material_key']}"
        for material in rejected["materials"]
    )
    manifest = json.loads((workspace / "workspace_manifest.json").read_text())
    assert keys == manifest["valid_declared_profile_keys"]
    assert len(keys) == len(set(keys))
    assert canonical_hash_without_self(rejected) != rejected["artifact_sha256"]
    assert manifest["expected_pool_load_reason"] == "artifact_hash_mismatch"
    assert manifest["expected_pool_route_state"] == "failed_declared"


def test_workspace_contains_exact_mutable_pair_and_preserves_profile(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    assert run_producer(workspace).returncode == 0
    files = sorted(path.relative_to(workspace).as_posix() for path in workspace.rglob("*") if path.is_file())
    assert files == [
        "generated/fl4512.compiled_pool.v1.json",
        "profiles/material_rendering_profiles.v1.json",
        "workspace_manifest.json",
    ]
    assert (workspace / PROFILE_RELATIVE).read_bytes() == SOURCE_PROFILE.read_bytes()


def test_producer_rejects_existing_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = run_producer(workspace)
    assert result.returncode == 2
    assert "workspace_must_not_exist" in result.stderr

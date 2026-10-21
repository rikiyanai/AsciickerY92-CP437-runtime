from __future__ import annotations

import sys
import subprocess
import tarfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from watchdog.phases.source_identity import (
    MULTIPLAYER_PROOF_MATERIALIZED_PATHS,
)
from watchdog.phases import source_identity


def test_source_materialization_has_no_clone_owner() -> None:
    source = Path(source_identity.__file__).read_text(encoding="utf-8")

    assert "git\", \"clone" not in source
    assert "sparse-checkout" not in source
    assert not hasattr(source_identity, "_clean_clone_command")


def test_multiplayer_materialization_keeps_only_required_nested_evidence() -> None:
    assert "docs/research/ascii/semantic_maps/upstream_xp_cell_contract" in (
        MULTIPLAYER_PROOF_MATERIALIZED_PATHS
    )
    assert "docs/research/ascii/verification/fl4482" in (
        MULTIPLAYER_PROOF_MATERIALIZED_PATHS
    )
    assert "docs/research/ascii/verification/fl4162" not in (
        MULTIPLAYER_PROOF_MATERIALIZED_PATHS
    )
    assert "docs/research/ascii/verification/fl4260" not in (
        MULTIPLAYER_PROOF_MATERIALIZED_PATHS
    )
    assert "godot_project" not in MULTIPLAYER_PROOF_MATERIALIZED_PATHS
    assert "pipeline-v3" not in MULTIPLAYER_PROOF_MATERIALIZED_PATHS
    assert ".scratch/canxkoz-texel-splatting" not in (
        MULTIPLAYER_PROOF_MATERIALIZED_PATHS
    )


def test_repository_archive_filter_rejects_parent_traversal() -> None:
    member = tarfile.TarInfo("../outside")

    try:
        source_identity._repository_archive_filter(member, "/snapshot")
    except RuntimeError as exc:
        assert "unsafe path" in str(exc)
    else:
        raise AssertionError("parent-traversal archive member was accepted")


def test_repository_archive_filter_preserves_relative_symlink() -> None:
    member = tarfile.TarInfo("pipeline-link")
    member.type = tarfile.SYMTYPE
    member.linkname = "../../docs/research/ascii/semantic_maps"

    assert source_identity._repository_archive_filter(member, "/snapshot") is member


def test_extract_git_archive_streams_exact_paths_and_preserves_symlink(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "source"
    destination = tmp_path / "snapshot"
    repo.mkdir()
    destination.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=repo, check=True
    )
    (repo / "kept").mkdir()
    (repo / "kept/payload.txt").write_text("exact\n", encoding="utf-8")
    (repo / "kept/link").symlink_to("payload.txt")
    (repo / "excluded.txt").write_text("excluded\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repo, check=True)

    source_identity._extract_git_archive(repo, "HEAD", destination, ["kept"])

    assert (destination / "kept/payload.txt").read_text(encoding="utf-8") == "exact\n"
    assert (destination / "kept/link").is_symlink()
    assert not (destination / "excluded.txt").exists()


def test_prepare_source_snapshot_omits_paths_absent_from_historical_commit(
    tmp_path: Path, monkeypatch,
) -> None:
    repo = tmp_path / "source"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=repo, check=True
    )
    (repo / ".gitmodules").write_text(
        '[submodule "unused"]\n\tpath = unused\n\turl = ../unused\n',
        encoding="utf-8",
    )
    (repo / "assets").mkdir()
    (repo / "assets/payload.txt").write_text("historical\n", encoding="utf-8")
    (repo / "build.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "historical"], cwd=repo, check=True)

    monkeypatch.setattr(
        source_identity,
        "MULTIPLAYER_PROOF_MATERIALIZED_PATHS",
        ("assets", "docs/future-only-receipt.json"),
    )

    prepared = source_identity.prepare_source_snapshot(repo, "HEAD")
    try:
        assert prepared.materialized_paths == ("assets",)
        assert prepared.omitted_materialized_paths == (
            "docs/future-only-receipt.json",
        )
        assert (prepared.repo_root / "assets/payload.txt").read_text(
            encoding="utf-8"
        ) == "historical\n"
        assert (prepared.repo_root / "build.sh").is_file()
    finally:
        source_identity.remove_prepared_source(prepared)

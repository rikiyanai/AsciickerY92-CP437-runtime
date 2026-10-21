"""Coverage contract for the all-evidence FL-4512 Prism archive."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "package_fl4512_prism_archive.py"
INVENTORY = ROOT / (
    "docs/research/ascii/verification/fl4512/"
    "2026-09-18-fl4512-prism-archive-inventory.json"
)
MANIFEST = ROOT / (
    "docs/research/ascii/verification/fl4512/"
    "2026-09-18-fl4512-extraction-manifest.json"
)
EXTRACT_SCRIPT = ROOT / "scripts" / "fl4512_extract_manifest.py"


def _extract_module():
    spec = importlib.util.spec_from_file_location("fl4512_extract_manifest", EXTRACT_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _module():
    spec = importlib.util.spec_from_file_location("package_fl4512_prism_archive", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def module():
    return _module()


@pytest.fixture(scope="module")
def inventory(module):
    return json.loads(INVENTORY.read_text(encoding="utf-8"))


def test_inventory_covers_sources_paper_articles_figures_receipts_and_skill(inventory) -> None:
    paths = {entry["archive_path"] for entry in inventory["entries"]}

    required = {
        "FL4512_PRISM_EXPORT/repo/apps/fl4512-runtime/engine/fl4512_active_stroke_owner.cpp",
        "FL4512_PRISM_EXPORT/repo/scripts/compile_fl4512_pools.py",
        "FL4512_PRISM_EXPORT/repo/apps/asciiid-classic/CLASSIC_PIN.md",
        "FL4512_PRISM_EXPORT/repo/docs/agent/glyph-rendering-paper/2026-07-06-fl4512-paper-draft.md",
        "FL4512_PRISM_EXPORT/repo/docs/research/ascii/articles/BIBLIOGRAPHY_ARCHIVE.md",
        "FL4512_PRISM_EXPORT/repo/articles/2026-09-08-fl4512-triptych-seam-election-media/MEDIA.md",
        "FL4512_PRISM_EXPORT/external/skill/SKILL.md",
    }
    assert required <= paths
    assert inventory["proof_status"] == "FL-4512 OPEN / Proof OPEN"


def test_inventory_complete_roots_have_no_missing_files(module, inventory) -> None:
    paths = {entry["archive_path"] for entry in inventory["entries"]}
    for root_rel in module.COMPLETE_REPO_ROOTS:
        for source in module._iter_tree_files(ROOT / root_rel):
            rel = source.relative_to(ROOT).as_posix()
            assert f"FL4512_PRISM_EXPORT/repo/{rel}" in paths


def test_inventory_excludes_generated_build_and_dependency_caches(inventory) -> None:
    paths = [entry["archive_path"] for entry in inventory["entries"]]
    for path in paths:
        parts = Path(path).parts
        assert ".run" not in parts
        assert "dist" not in parts
        assert "node_modules" not in parts
        assert "__pycache__" not in parts
        assert not any(part.startswith((".o_", ".d_", ".venv")) for part in parts)


def test_safe_output_rejects_repository_and_broad_destinations(module, tmp_path: Path) -> None:
    for bad in (ROOT / "archive.tar.zst", Path.home(), Path.home() / "Downloads"):
        try:
            module._safe_output(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe path accepted: {bad}")
    assert module._safe_output(tmp_path / "fl4512.tar.zst").is_absolute()


def test_snapshot_copy_retries_transient_timeout(module, tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.bin"
    destination = tmp_path / "destination.bin"
    source.write_bytes(b"frozen FL-4512 evidence")
    real_open = Path.open
    attempts = 0

    def flaky_open(path: Path, *args, **kwargs):
        nonlocal attempts
        if path == source and attempts == 0:
            attempts += 1
            raise TimeoutError(60, "transient source timeout")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", flaky_open)
    module._copy_snapshot(source, destination)

    assert attempts == 1
    assert destination.read_bytes() == source.read_bytes()


def test_archive_validation_allows_long_distance_window(module, tmp_path: Path, monkeypatch) -> None:
    archive = tmp_path / "fl4512.tar.zst"
    archive.touch()
    calls = []

    def capture_run(args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(module.subprocess, "run", capture_run)
    module._validate_archive(archive)

    assert calls == [(["zstd", "-t", "--long=31", str(archive)], {"check": True})]


def test_inventory_covers_every_source_profile_path(inventory) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    repo_paths = {
        entry["source_path"] for entry in inventory["entries"] if entry["origin"] == "repository"
    }
    missing = [path for path in manifest["files"] if path not in repo_paths]
    assert missing == []


def test_inventory_covers_presenter_makefile_closure(inventory) -> None:
    extract = _extract_module()
    repo_paths = {
        entry["source_path"] for entry in inventory["entries"] if entry["origin"] == "repository"
    }
    parsed: set[str] = set()
    for name in extract.PRESENTER_MAKEFILES:
        parsed.update(extract.parse_makefile_srcs(ROOT / name))
    assert len(parsed) > 120, "both presenter makefiles must contribute SRCS entries"
    missing = sorted(path for path in parsed if path not in repo_paths)
    assert missing == []
    for root in extract.PRESENTER_RUNTIME_ROOTS:
        assert any(
            path == root or path.startswith(root + "/") for path in repo_paths
        ), root


def test_inventory_pins_epoch_single_owner_current_runtime(inventory) -> None:
    repo_paths = {
        entry["source_path"] for entry in inventory["entries"] if entry["origin"] == "repository"
    }
    assert "apps/fl4512-runtime/engine/fl4693_runtime_epoch.h" in repo_paths
    assert "engine/render/fl4693_runtime_epoch.h" not in repo_paths

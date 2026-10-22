"""FL-4772: an aspect-free canonical pool must not be publishable.

The compiler used to treat ``--aspect-authoring`` as an optional extra. Omitting
it skipped aspect compilation silently and still emitted a well-formed pool
carrying ``aspect_assets=[]``, ``aspect_rows=[]`` and
``quantization_version=null``. That artifact satisfied every other gate, so an
aspect-free pool reached publication with nothing failing (FL-4512).

These tests pin the closed door from both sides: the canonical invocation must
carry aspect content even when the flag is not typed, and an emptied supply must
abort before anything is written.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMPILER = ROOT / "scripts/compile_fl4512_pools.py"
CANONICAL_ASPECT = ROOT / "assets/glyphs/profiles/fl4512.aspect_pool_authoring.v1.json"


def _load_compiler():
    spec = importlib.util.spec_from_file_location("fl4512_compiler_probe", COMPILER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _compile(out: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(COMPILER), "--out", str(out), *extra],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def test_canonical_aspect_supply_is_tracked() -> None:
    # The default the compiler now points at must actually exist; otherwise the
    # mandatory path would fail closed for the wrong reason.
    assert CANONICAL_ASPECT.is_file()
    authoring = json.loads(CANONICAL_ASPECT.read_text(encoding="utf-8"))
    assert authoring.get("assets"), "canonical aspect authoring carries no assets"


def test_omitting_the_flag_still_compiles_aspect_content(tmp_path: Path) -> None:
    # The regression: this exact invocation — no --aspect-authoring — is what
    # published the aspect-free pool. It must now carry aspect content.
    out = tmp_path / "pool.json"
    result = _compile(out)
    assert result.returncode == 0, result.stderr
    artifact = json.loads(out.read_text(encoding="utf-8"))
    assert artifact["aspect_assets"], "aspect assets missing from a default compile"
    assert artifact["aspect_rows"], "aspect rows missing from a default compile"
    assert artifact["header"]["quantization_version"] is not None
    assert artifact["header"]["aspect_golden_fixture_sha256"] is not None


def test_default_matches_an_explicit_canonical_supply(tmp_path: Path) -> None:
    # Defaulting must be identical to typing the canonical path, so the flag
    # cannot become a second, divergent supply.
    implicit = tmp_path / "implicit.json"
    explicit = tmp_path / "explicit.json"
    assert _compile(implicit).returncode == 0
    assert _compile(explicit, "--aspect-authoring", str(CANONICAL_ASPECT)).returncode == 0
    assert implicit.read_bytes() == explicit.read_bytes()


def test_empty_aspect_supply_fails_closed_and_writes_nothing(tmp_path: Path) -> None:
    # An emptied flag is the only remaining way to reach the compiler with no
    # aspect supply. It must abort, and must not leave a partial artifact.
    out = tmp_path / "pool.json"
    result = _compile(out, "--aspect-authoring", "")
    assert result.returncode != 0
    assert not out.exists(), "an aspect-free compile left an artifact behind"


def test_missing_aspect_supply_path_fails_closed(tmp_path: Path) -> None:
    out = tmp_path / "pool.json"
    result = _compile(out, "--aspect-authoring", str(tmp_path / "absent.json"))
    assert result.returncode != 0
    assert not out.exists()


def test_publication_refuses_an_aspect_free_artifact(tmp_path, monkeypatch, capsys):
    # FL-4772 defense in depth at the publication boundary: however an
    # aspect-free artifact is produced, it must not be written. compile_artifact
    # stays permissive for focused unit tests, so this is the refusal that holds.
    module = _load_compiler()
    out = tmp_path / "pool.json"
    monkeypatch.setattr(
        module,
        "compile_artifact",
        lambda *args, **kwargs: {
            "header": {"quantization_version": None},
            "rows": [],
            "aspect_assets": [],
            "aspect_rows": [],
            "artifact_sha256": "unused",
        },
    )
    monkeypatch.setattr(sys, "argv", ["compile_fl4512_pools.py", "--out", str(out)])
    assert module.main() == 2
    assert not out.exists()
    assert "aspect_free_pool_not_publishable" in capsys.readouterr().out


def test_aspect_bearing_artifact_still_publishes(tmp_path, monkeypatch, capsys):
    # FL-4772: the guard must reject only aspect-free pools, not every pool.
    module = _load_compiler()
    out = tmp_path / "pool.json"
    monkeypatch.setattr(
        module,
        "compile_artifact",
        lambda *args, **kwargs: {
            "header": {"quantization_version": 3},
            "rows": [],
            "aspect_assets": [{"asset_id": "a"}],
            "aspect_rows": [{"row": 0}],
            "artifact_sha256": "unused",
        },
    )
    monkeypatch.setattr(sys, "argv", ["compile_fl4512_pools.py", "--out", str(out)])
    assert module.main() == 0
    assert out.exists()

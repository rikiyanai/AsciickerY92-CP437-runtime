#!/usr/bin/env python3
"""Guard the FL-4431 Baseline A v2 cut outside the final package chain.

The canonical grid and acceptance package have their own integration tests.
This file covers the three other owners changed by the same delete-first cut:
the compositor A/B launcher, the JavaScript desktop proof, and the repository
canon guard.  The tests deliberately resurrect the retired active v1 schema
and remove each shared-validator call so a green result demonstrates that the
guard rejects the real regression shapes rather than merely finding v2 words.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
LAUNCHER_PATH = SCRIPTS / "launch_godot_baseline_compositor_ab.py"
JS_PROOF_PATH = SCRIPTS / "proofs" / "proof_colyseus_desktop_visual_sequence.js"
CANON_PATH = SCRIPTS / "validate_godot_visual_grid_canon.py"


def _load_module(path: Path, name: str) -> ModuleType:
    """Load one repository script without executing its command-line entry.

    ``path`` identifies the script and ``name`` gives the temporary import a
    unique module key.  The helper returns the loaded module so tests can call
    public functions while avoiding a headed Godot launch.
    """

    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot import module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _validated_v2_fixture(tmp_path: Path) -> dict[str, Any]:
    """Return the nested facts the shared validator gives to the A/B launcher.

    The validator itself has exhaustive filesystem and hash mutation coverage
    in ``test_fl4431_baseline_receipt_v2.py``.  This fixture isolates the
    downstream normalization seam: the launcher must read v2 nesting exactly
    and must not recreate the predecessor's flat aliases.
    """

    source_root = (tmp_path / "baseline").resolve()
    server_root = (tmp_path / "server").resolve()
    return {
        "schema": "asciicker.godot_baseline.v2",
        "title": "BASELINE A FL-4431 operator-designated fixture",
        "designation": {"operator_identity": "fixture-operator"},
        "baseline_source": {
            "root": str(source_root),
            "commit": "a" * 40,
            "tree": "b" * 40,
            "project_subdir": "godot_project",
        },
        "proof_tool_source": {
            "commit": "c" * 40,
            "tree": "d" * 40,
        },
        "lane_manifest": {
            "path": str((tmp_path / "lane.json").resolve()),
            "sha256": "e" * 64,
            "git_blob": "f" * 40,
        },
        "capture_contract": {
            "capture_id": "capture-v2-fixture",
            "temporal_capture_id": "temporal-v2-fixture",
            "server_identity": {"server_source_root": str(server_root)},
            "features": ["terrain", "water", "trees"],
        },
        "self_hash": {"digest": "1" * 64},
    }


def test_python_launcher_consumes_validated_nested_v2(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Prove the Python launcher installs only validator-returned v2 facts."""

    sys.path.insert(0, str(SCRIPTS))
    try:
        launcher = _load_module(LAUNCHER_PATH, "fl4431_baseline_ab_v2_test")
    finally:
        sys.path.remove(str(SCRIPTS))
    validated = _validated_v2_fixture(tmp_path)
    receipt_path = tmp_path / "baseline-v2.json"
    monkeypatch.setattr(launcher, "validate_receipt", lambda path: validated)

    launcher.configure_receipted_baseline(receipt_path)

    assert launcher.PROMOTED_BASELINE_ARCHIVE_DIR == Path(
        validated["baseline_source"]["root"]
    )
    assert launcher.PROMOTED_BASELINE_PROJECT == (
        Path(validated["baseline_source"]["root"]) / "godot_project"
    )
    assert launcher.PROMOTED_BASELINE_SERVER == Path(
        validated["capture_contract"]["server_identity"]["server_source_root"]
    )
    assert launcher.PROMOTED_BASELINE_SOURCE_IDENTITY == "a" * 40
    assert launcher.PROMOTED_BASELINE_FEATURES == ["terrain", "water", "trees"]
    assert launcher.PROMOTED_BASELINE_CONTRACT["receipt_self_hash"] == "1" * 64
    assert launcher.PROMOTED_BASELINE_CONTRACT["source_tree"] == "b" * 40
    assert launcher.PROMOTED_BASELINE_CONTRACT["capture_id"] == "capture-v2-fixture"
    assert launcher.PROMOTED_BASELINE_CONTRACT["proof_tool_commit"] == "c" * 40


def test_python_launcher_rejects_active_v1_receipt(tmp_path: Path) -> None:
    """Prove a predecessor receipt cannot be selected as active Baseline A."""

    sys.path.insert(0, str(SCRIPTS))
    try:
        launcher = _load_module(LAUNCHER_PATH, "fl4431_baseline_ab_v1_reject_test")
    finally:
        sys.path.remove(str(SCRIPTS))
    predecessor = tmp_path / "baseline-v1.json"
    predecessor.write_text(
        '{"schema":"asciicker.godot_baseline.v1","source_commit":"'
        + ("a" * 40)
        + '"}\n',
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="BASELINE_RECEIPT_V2_INVALID"):
        launcher.configure_receipted_baseline(predecessor)


def test_owned_active_receipt_sources_obey_v2_cut() -> None:
    """Check every file owned by this slice against the executable canon gate."""

    sys.path.insert(0, str(SCRIPTS))
    try:
        canon = _load_module(CANON_PATH, "fl4431_visual_grid_canon_v2_test")
    finally:
        sys.path.remove(str(SCRIPTS))
    owned = {
        "scripts/launch_godot_baseline_compositor_ab.py": LAUNCHER_PATH,
        "scripts/proofs/proof_colyseus_desktop_visual_sequence.js": JS_PROOF_PATH,
    }
    for relative, path in owned.items():
        errors = canon.validate_active_receipt_owner_text(
            path.read_text(encoding="utf-8"),
            rel=relative,
        )
        assert errors == [], "\n".join(errors)


@pytest.mark.parametrize(
    ("relative", "path"),
    [
        ("scripts/launch_godot_baseline_compositor_ab.py", LAUNCHER_PATH),
        (
            "scripts/proofs/proof_colyseus_desktop_visual_sequence.js",
            JS_PROOF_PATH,
        ),
    ],
)
def test_canon_guard_rejects_resurrected_active_v1(
    relative: str,
    path: Path,
) -> None:
    """Inject the retired schema into each active owner and require rejection."""

    sys.path.insert(0, str(SCRIPTS))
    try:
        canon = _load_module(CANON_PATH, "fl4431_visual_grid_canon_v1_mutant")
    finally:
        sys.path.remove(str(SCRIPTS))
    mutant = path.read_text(encoding="utf-8") + (
        '\nRETIRED_MUTANT = "asciicker.godot_baseline.v1"\n'
    )
    errors = canon.validate_active_receipt_owner_text(mutant, rel=relative)
    assert any("retired active Baseline A schema resurrected" in error for error in errors)


@pytest.mark.parametrize(
    ("relative", "path", "required_call"),
    [
        (
            "scripts/launch_godot_baseline_compositor_ab.py",
            LAUNCHER_PATH,
            "validated = validate_receipt(receipt_path.expanduser().resolve())",
        ),
        (
            "scripts/proofs/proof_colyseus_desktop_visual_sequence.js",
            JS_PROOF_PATH,
            "[BASELINE_RECEIPT_VALIDATOR, 'validate', receiptPath]",
        ),
    ],
)
def test_canon_guard_rejects_removed_shared_validator_call(
    relative: str,
    path: Path,
    required_call: str,
) -> None:
    """Delete the real validation call and prove token-only v2 prose cannot pass."""

    sys.path.insert(0, str(SCRIPTS))
    try:
        canon = _load_module(CANON_PATH, "fl4431_visual_grid_canon_call_mutant")
    finally:
        sys.path.remove(str(SCRIPTS))
    source = path.read_text(encoding="utf-8")
    assert required_call in source
    mutant = source.replace(required_call, "VALIDATOR_CALL_DELETED_BY_MUTANT", 1)
    errors = canon.validate_active_receipt_owner_text(mutant, rel=relative)
    assert any("missing shared v2 validator integration text" in error for error in errors)


def test_javascript_proof_remains_parseable() -> None:
    """Run Node's parser without starting the server, clients, or Godot."""

    result = subprocess.run(
        ["node", "--check", str(JS_PROOF_PATH)],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_javascript_proof_rejects_active_v1_before_launch(tmp_path: Path) -> None:
    """Exercise the JS-to-Python validator seam with a real rejected document."""

    predecessor = tmp_path / "baseline-v1.json"
    predecessor.write_text(
        '{"schema":"asciicker.godot_baseline.v1","source_commit":"'
        + ("a" * 40)
        + '"}\n',
        encoding="utf-8",
    )
    receipt_output = tmp_path / "desktop-visual-receipt.json"
    environment = dict(os.environ)
    environment["FL4377_BASELINE_RECEIPT"] = str(predecessor)
    environment["COLYSEUS_DESKTOP_VISUAL_SEQUENCE_RECEIPT"] = str(receipt_output)
    result = subprocess.run(
        ["node", str(JS_PROOF_PATH)],
        cwd=ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        check=False,
    )
    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert "invalid FL-4377 Baseline A v2 receipt" in combined
    assert "FL4431_BASELINE_RECEIPT_V2_REJECTED" in combined
    # Validation runs before the output directory and server process are
    # created, so an invalid predecessor cannot leave a plausible run receipt.
    assert not receipt_output.exists()


def test_canon_cli_rejects_v1_in_active_owner() -> None:
    """Drive the same stdin mode used by hook mutation tests."""

    source = LAUNCHER_PATH.read_text(encoding="utf-8")
    mutant = source + '\nRETIRED_MUTANT = "asciicker.godot_baseline.v1"\n'
    result = subprocess.run(
        [
            sys.executable,
            str(CANON_PATH),
            "--stdin-content",
            "--active-receipt-owner",
            "--label",
            "scripts/launch_godot_baseline_compositor_ab.py",
        ],
        cwd=ROOT,
        input=mutant,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 1
    assert "retired active Baseline A schema resurrected" in result.stdout

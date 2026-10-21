#!/usr/bin/env python3
"""FL-4512 G4: write_native_build_identity.py is the single writer cut.

Valid and invalid binary/renderer/target tuples resolve through
``resolve_identity``; the full CLI path (containment, tuple validation,
atomic sidecar, hash provenance) runs against a temporary binary under a
temporary repo root. Rejections exit nonzero and write no sidecar.

The writer admits only the two production routes (``game-fl4512`` and
``game-classic``). The ``game`` Makefile alias is not a writer route: no
production callsite passes ``--make-target game``, so it is rejected here
and this test never reads the G3-owned root Makefile.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
WRITER = ROOT / "scripts" / "write_native_build_identity.py"


def _load_writer():
    spec = importlib.util.spec_from_file_location(
        "write_native_build_identity", WRITER
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MOD = _load_writer()


def test_valid_tuples_resolve() -> None:
    assert MOD.resolve_identity(
        ".run/game", "fl4512_unicode", "game-fl4512"
    ) == (".run/game", "fl4512_unicode", "game-fl4512")
    assert MOD.resolve_identity(
        ".run/game_classic", "classic_cp437", "game-classic"
    ) == (".run/game_classic", "classic_cp437", "game-classic")


def test_invalid_tuples_rejected() -> None:
    with pytest.raises(ValueError):
        MOD.resolve_identity(
            ".run/game", "classic_cp437", "game-fl4512"
        )
    with pytest.raises(ValueError):
        MOD.resolve_identity(
            ".run/game_classic", "fl4512_unicode", "game-classic"
        )
    with pytest.raises(ValueError):
        MOD.resolve_identity(
            ".run/game", "fl4512_unicode", "game"
        )
    with pytest.raises(ValueError):
        MOD.resolve_identity(
            ".run/game_classic", "classic_cp437", "game"
        )
    with pytest.raises(ValueError):
        MOD.resolve_identity(
            ".run/game", "fl4512_unicode", "game-classic"
        )
    with pytest.raises(ValueError):
        MOD.resolve_identity(
            ".run/game_classic", "classic_cp437", "game-fl4512"
        )
    with pytest.raises(ValueError):
        MOD.resolve_identity(
            ".run/game_term", "fl4512_unicode", "terminal"
        )


def test_write_identity_hashes_temp_binary_and_leaves_no_tmp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MOD, "ROOT", tmp_path)
    binary = tmp_path / "game_classic"
    binary.write_bytes(b"\x7fELF-fake-classic")
    out = MOD.write_identity(binary, "classic_cp437", "game-classic", "deadbeef")
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["binary_sha256"] == hashlib.sha256(
        b"\x7fELF-fake-classic"
    ).hexdigest()
    assert payload["source_commit"] == "deadbeef"
    assert payload["schema"] == "launcher.native_build_identity.v1"
    assert not out.with_name(out.name + ".tmp").exists()

def test_cli_valid_and_invalid_against_temp_repo_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = tmp_path / ".run"
    run_dir.mkdir()
    binary = run_dir / "game_classic"
    binary.write_bytes(b"\x7fELF-fake-classic")
    monkeypatch.setattr(MOD, "ROOT", tmp_path)
    monkeypatch.setattr(MOD, "git_head", lambda: "deadbeef")

    assert MOD.main(
        ["--binary", ".run/game_classic",
         "--renderer", "classic_cp437", "--make-target", "game-classic"]
    ) == 0
    sidecar = run_dir / "game_classic.build_identity.json"
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert payload["binary_sha256"] == hashlib.sha256(
        b"\x7fELF-fake-classic"
    ).hexdigest()
    material = "\n".join(
        ("deadbeef", payload["binary_sha256"],
         "classic_cp437", "game-classic")
    ).encode()
    assert payload["build_hash"] == hashlib.sha256(material).hexdigest()
    assert payload["renderer"] == "classic_cp437"
    assert payload["binary_path"] == ".run/game_classic"
    assert not sidecar.with_name(sidecar.name + ".tmp").exists()

    sidecar.unlink()
    with pytest.raises(SystemExit) as exc:
        MOD.main(
            ["--binary", ".run/game_classic",
             "--renderer", "fl4512_unicode", "--make-target", "game-classic"]
        )
    assert exc.value.code != 0
    assert not sidecar.exists()

    with pytest.raises(SystemExit) as exc:
        MOD.main(
            ["--binary", ".run/game_classic",
             "--renderer", "classic_cp437", "--make-target", "game"]
        )
    assert exc.value.code != 0
    assert not sidecar.exists()

    with pytest.raises(SystemExit) as exc:
        MOD.main(
            ["--binary", "../outside", "--renderer",
             "classic_cp437", "--make-target", "game-classic"]
        )
    assert exc.value.code != 0

    with pytest.raises(SystemExit) as exc:
        MOD.main(
            ["--binary", ".run/game", "--renderer",
             "fl4512_unicode", "--make-target", "game-fl4512"]
        )
    assert exc.value.code != 0

def test_failed_cli_write_preserves_existing_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = tmp_path / ".run"
    run_dir.mkdir()
    binary = run_dir / "game_classic"
    binary.write_bytes(b"\x7fELF-fake-classic")
    monkeypatch.setattr(MOD, "ROOT", tmp_path)
    monkeypatch.setattr(MOD, "git_head", lambda: "deadbeef")

    assert MOD.main(
        ["--binary", ".run/game_classic",
         "--renderer", "classic_cp437", "--make-target", "game-classic"]
    ) == 0
    sidecar = run_dir / "game_classic.build_identity.json"
    before = sidecar.read_bytes()

    with pytest.raises(SystemExit) as exc:
        MOD.main(
            ["--binary", ".run/game_classic",
             "--renderer", "fl4512_unicode", "--make-target", "game-classic"]
        )
    assert exc.value.code != 0
    assert sidecar.read_bytes() == before
    assert not sidecar.with_name(sidecar.name + ".tmp").exists()


def test_symlink_escape_rejected_without_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    run_dir = repo / ".run"
    run_dir.mkdir(parents=True)
    outside = tmp_path / "outside_bin"
    outside.write_bytes(b"\x7fELF-outside")
    (run_dir / "game_classic").symlink_to(outside)
    monkeypatch.setattr(MOD, "ROOT", repo)
    monkeypatch.setattr(MOD, "git_head", lambda: "deadbeef")

    with pytest.raises(SystemExit) as exc:
        MOD.main(
            ["--binary", ".run/game_classic",
             "--renderer", "classic_cp437", "--make-target", "game-classic"]
        )
    assert exc.value.code != 0
    assert not (run_dir / "game_classic.build_identity.json").exists()


def test_writer_map_is_exact_and_admits_no_alias() -> None:
    assert set(MOD.BINARY_RENDERER) == {".run/game", ".run/game_classic"}
    assert MOD.BINARY_RENDERER[".run/game"] == "fl4512_unicode"
    assert MOD.BINARY_RENDERER[".run/game_classic"] == "classic_cp437"
    assert MOD.BINARY_TARGETS[".run/game"] == frozenset({"game-fl4512"})
    assert MOD.BINARY_TARGETS[".run/game_classic"] == frozenset({"game-classic"})
    for allowed in MOD.BINARY_TARGETS.values():
        assert len(allowed) == 1
        assert "game" not in allowed


def test_dead_makefile_diagnostic_helper_removed() -> None:
    assert not hasattr(MOD, "game_routes_to_classic")
    assert not hasattr(MOD, "GAME_ROUTES_CLASSIC_RE")
    text = WRITER.read_text(encoding="utf-8")
    assert "game_routes_to_classic" not in text
    assert "GAME_ROUTES_CLASSIC_RE" not in text
    assert "\nimport re\n" not in text

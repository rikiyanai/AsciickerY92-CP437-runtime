#!/usr/bin/env python3
"""FL-4689 native builds produce launcher-verifiable identities."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_root_makefile_writes_identity_after_each_native_game_build() -> None:
    text = (ROOT / "Makefile").read_text()
    assert "--binary .run/game --renderer fl4512_unicode --make-target game-fl4512" in text
    assert "--binary .run/game_classic --renderer classic_cp437 --make-target game-classic" in text


def test_identity_writer_uses_binary_hash_and_head() -> None:
    text = (ROOT / "scripts" / "write_native_build_identity.py").read_text()
    assert '["git", "rev-parse", "HEAD"]' in text
    assert '"binary_sha256": binary_hash' in text
    assert '"build_hash": hashlib.sha256(build_material).hexdigest()' in text
    assert 'binary.name + ".build_identity.json"' in text


def test_identity_writer_owns_single_binary_renderer_map() -> None:
    text = (ROOT / "scripts" / "write_native_build_identity.py").read_text()
    assert '".run/game": "fl4512_unicode"' in text
    assert '".run/game_classic": "classic_cp437"' in text
    assert "resolve_identity" in text
    assert "outside the repo" in text

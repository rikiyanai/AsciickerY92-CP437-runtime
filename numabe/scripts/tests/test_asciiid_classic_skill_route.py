"""Classic's local agent route is the one intentional app-local skill exception."""

from __future__ import annotations

from pathlib import Path

from scripts.maintainer.hooks import md_file_ban_hook


ROOT = Path(__file__).resolve().parents[2]


def test_classic_skill_is_discoverable_and_explicitly_allowed() -> None:
    skill = ROOT / "apps/asciiid-classic/SKILL.md"
    assert skill.is_file()
    assert skill.read_text(encoding="utf-8").startswith("---\nname: asciiid-classic\n")
    assert md_file_ban_hook._is_allowed(str(skill))
    assert not md_file_ban_hook._is_allowed("apps/asciicker-presenter/SKILL.md")

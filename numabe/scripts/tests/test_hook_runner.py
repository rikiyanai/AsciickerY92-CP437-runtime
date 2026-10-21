"""The fail-closed launcher is the one hook that cannot be guarded by a hook.

FL-4853: on 2026-09-19 md_file_ban_hook.py was caught mid-refactor, raised
NameError, exited 1, and the host read that as "allow" — a forbidden Write
landed. These tests pin the exit-code contract that makes that impossible.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts/maintainer/hooks/_hook_runner.py"

ALLOW, BLOCK = 0, 2
PAYLOAD = json.dumps({"tool_name": "Write", "tool_input": {"file_path": "x.md"}})


def _run(target: str | Path, *, env_extra: dict[str, str] | None = None):
    import os

    env = dict(os.environ)
    env.pop("Y9_HOOK_FAIL_OPEN", None)
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, str(RUNNER), str(target)],
        input=PAYLOAD, text=True, capture_output=True, env=env, timeout=60,
    )


def _hook(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "fake_hook.py"
    path.write_text(body)
    return path


def test_allow_is_relayed(tmp_path: Path) -> None:
    assert _run(_hook(tmp_path, "raise SystemExit(0)\n")).returncode == ALLOW


def test_block_is_relayed(tmp_path: Path) -> None:
    hook = _hook(tmp_path, "import sys\nsys.stderr.write('NOPE\\n')\nraise SystemExit(2)\n")
    proc = _run(hook)
    assert proc.returncode == BLOCK
    assert "NOPE" in proc.stderr, "the hook's own message must reach the agent"


def test_crashing_hook_blocks_instead_of_allowing(tmp_path: Path) -> None:
    """The exact 03:26 failure: a helper the same edit had already deleted."""
    hook = _hook(tmp_path, "def main():\n    return _gone()\nmain()\n")
    proc = _run(hook)
    assert proc.returncode == BLOCK
    assert "did not run correctly" in proc.stderr


def test_syntax_error_blocks(tmp_path: Path) -> None:
    """A half-saved file is not valid Python and must not mean 'allow'."""
    assert _run(_hook(tmp_path, "def main(:\n")).returncode == BLOCK


@pytest.mark.parametrize("code", [1, 3, 7, 127])
def test_any_unexpected_exit_code_blocks(tmp_path: Path, code: int) -> None:
    assert _run(_hook(tmp_path, f"raise SystemExit({code})\n")).returncode == BLOCK


def test_missing_hook_blocks(tmp_path: Path) -> None:
    assert _run(tmp_path / "not_here.py").returncode == BLOCK


def test_escape_hatch_downgrades_to_allow(tmp_path: Path) -> None:
    hook = _hook(tmp_path, "def main():\n    return _gone()\nmain()\n")
    proc = _run(hook, env_extra={"Y9_HOOK_FAIL_OPEN": "1"})
    assert proc.returncode == ALLOW
    assert "allowing anyway" in proc.stderr


def test_real_ban_hook_still_decides_through_the_runner() -> None:
    """End to end: the launcher must not change a healthy hook's verdict."""
    hook = ROOT / "scripts/maintainer/hooks/md_file_ban_hook.py"
    import os

    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(ROOT)
    env.pop("Y9_HOOK_FAIL_OPEN", None)

    def verdict(rel: str) -> int:
        payload = json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(ROOT / rel)}})
        return subprocess.run(
            [sys.executable, str(RUNNER), str(hook)],
            input=payload, text=True, capture_output=True, env=env, timeout=60,
        ).returncode

    assert verdict("scripts/_runner_probe.md") == BLOCK
    assert verdict("docs/agent/_runner_probe.md") == ALLOW

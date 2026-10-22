"""FL-4512: picocad artist guide Step 2 stays copy/paste-valid Classic batch."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / "docs" / "picocad-artist-guide.md"


def _sh_blocks() -> list[str]:
    return re.findall(r"```sh\n(.*?)```", GUIDE.read_text(encoding="utf-8"), re.DOTALL)


def _step2_block() -> str:
    step2 = GUIDE.read_text(encoding="utf-8").split("## Step 2:")[1].split("---")[0]
    match = re.search(r"```sh\n(.*?)```", step2, re.DOTALL)
    assert match is not None, "Step 2 must carry one fenced sh placement block"
    return match.group(1)


def test_all_guide_shell_blocks_parse() -> None:
    blocks = _sh_blocks()
    assert len(blocks) >= 1
    for block in blocks:
        result = subprocess.run(
            ["bash", "-n", "/dev/stdin"],
            input=block,
            text=True,
            capture_output=True,
            timeout=30,
        )
        assert result.returncode == 0, f"guide shell block fails bash -n: {block!r}"


def test_step2_has_no_angle_bracket_metavariables() -> None:
    block = _step2_block()
    assert not re.search(r"<[A-Za-z][A-Za-z0-9_./-]*>", block), (
        f"Step 2 block uses shell-redirection metavariables: {block!r}"
    )


def test_step2_saves_to_distinct_output() -> None:
    block = _step2_block()
    assert "input.a3d" in block and "placed.a3d" in block
    assert "SAVE_MAP %s" in block or "SAVE_MAP \"$workdir/placed.a3d\"" in block
    assert "--map \"$workdir/input.a3d\"" in block


def test_step2_names_witnesses_and_falsifier() -> None:
    step2 = GUIDE.read_text(encoding="utf-8").split("## Step 2:")[1].split("---")[0]
    assert "[MCP] Success: Placed mesh" in step2
    assert "[MCP] Map saved:" in step2
    assert "[MCP] Error" in step2

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HOOK_PATH = ROOT / "scripts/maintainer/hooks/md_file_ban_hook.py"


def _load_hook():
    spec = importlib.util.spec_from_file_location("md_file_ban", HOOK_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


HOOK = _load_hook()


def _allow(monkeypatch, root: Path, rel: str) -> bool:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.delenv("CODEX_PROJECT_DIR", raising=False)
    return HOOK._is_allowed(str(root / rel))


def test_root_agents_md_is_allowed(monkeypatch, tmp_path: Path) -> None:
    assert _allow(monkeypatch, tmp_path, "AGENTS.md") is True


def test_hidden_dir_agents_md_is_blocked(monkeypatch, tmp_path: Path) -> None:
    assert _allow(monkeypatch, tmp_path, ".xx/AGENTS.md") is False


def test_lane_write_is_allowed(monkeypatch, tmp_path: Path) -> None:
    assert _allow(monkeypatch, tmp_path, "docs/agent/notes.md") is True


def test_traversal_out_of_lane_is_blocked(monkeypatch, tmp_path: Path) -> None:
    assert _allow(monkeypatch, tmp_path, "docs/agent/../../.xx/notes.txt") is False


def test_mimic_lane_under_hidden_dir_is_blocked(monkeypatch, tmp_path: Path) -> None:
    assert _allow(monkeypatch, tmp_path, ".xx/docs/agent/notes.txt") is False


def test_root_stray_is_blocked(monkeypatch, tmp_path: Path) -> None:
    assert _allow(monkeypatch, tmp_path, "stray.md") is False


def test_agents_txt_has_no_basename_exception(monkeypatch, tmp_path: Path) -> None:
    assert _allow(monkeypatch, tmp_path, "AGENTS.txt") is False

def test_markdown_ext_is_blocked_like_md(monkeypatch, tmp_path: Path) -> None:
    assert HOOK._is_banned_ext(".xx/notes.markdown") is True
    assert _allow(monkeypatch, tmp_path, ".xx/notes.markdown") is False
    assert _allow(monkeypatch, tmp_path, "docs/agent/ok.markdown") is True


# --- FL-4853: doors other than the Claude Write tool -------------------------

def _verdict(root: Path, payload: dict) -> int:
    import json
    import os
    import subprocess

    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(root)
    env.pop("CODEX_PROJECT_DIR", None)
    return subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=json.dumps(payload), text=True, capture_output=True, env=env, timeout=60,
    ).returncode


def _patch(body: str) -> dict:
    return {"tool_name": "apply_patch", "tool_input": {"input": body}}


def test_case_aliased_root_still_recognises_the_lane(monkeypatch, tmp_path: Path) -> None:
    """This checkout is reachable as .../Downloads/asciicker-Y9-2 and
    .../downloads/asciicker-y9-2, and the volume is case-insensitive. A
    case-sensitive prefix test called in-repo paths 'outside the repo' and
    blocked legitimate lane writes."""
    lane = tmp_path / "docs/agent"
    lane.mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("CODEX_PROJECT_DIR", raising=False)
    HOOK._CASE_INSENSITIVE.clear()

    swapped = str(tmp_path).upper()
    if not Path(swapped).is_dir():
        import pytest

        pytest.skip("case-sensitive filesystem: the alias cannot arise here")
    assert HOOK._is_allowed(swapped + "/docs/agent/notes.md") is True
    assert HOOK._is_allowed(swapped + "/scripts/notes.md") is False


def test_codex_apply_patch_adding_a_stray_doc_is_blocked(tmp_path: Path) -> None:
    assert _verdict(tmp_path, _patch("*** Begin Patch\n*** Add File: notes.md\n+hi\n")) == 2


def test_codex_apply_patch_adding_hidden_agents_md_is_blocked(tmp_path: Path) -> None:
    assert _verdict(tmp_path, _patch("*** Begin Patch\n*** Add File: .xx/AGENTS.md\n+hi\n")) == 2


def test_codex_apply_patch_into_the_lane_is_allowed(tmp_path: Path) -> None:
    assert _verdict(tmp_path, _patch("*** Begin Patch\n*** Add File: docs/agent/ok.md\n+hi\n")) == 0


def test_codex_apply_patch_updating_an_existing_file_is_allowed(tmp_path: Path) -> None:
    assert _verdict(tmp_path, _patch("*** Begin Patch\n*** Update File: README.md\n+hi\n")) == 0


def test_unified_diff_new_file_is_blocked(tmp_path: Path) -> None:
    assert _verdict(tmp_path, _patch("--- /dev/null\n+++ b/stray.md\n+hi\n")) == 2


def test_outside_repo_docs_are_blocked_on_purpose(monkeypatch, tmp_path: Path) -> None:
    """Intended, not incidental: the module docstring lists "/tmp/" among the
    blocked locations. A scratch .md is exactly the disposable report that
    doc-hygiene forbids as a durable owner, so the ban covers it. Use a
    non-doc suffix (.log, .bak) for genuine scratch files.
    """
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("CODEX_PROJECT_DIR", raising=False)
    HOOK._CASE_INSENSITIVE.clear()
    assert HOOK._is_allowed("/private/tmp/scratch/report.md") is False
    assert HOOK._is_banned_ext("/private/tmp/scratch/report.bak") is False

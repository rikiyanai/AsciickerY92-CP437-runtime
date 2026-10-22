"""Host-shaped payload gate for the placement guard.

Operator directive, 2026-09-19, after three false-positive classes in one
session: a path-only matrix is insufficient. The guard must be exercised with
the payload shapes the hosts actually send, end to end through the real hook
scripts, and it must decide whether a call is a WRITE before interpreting any
value as a repository path.

What each class of bug here would have caught:
  - `Read skill://doc-hygiene` refused as a document, breaking skill loading.
    Caught by: read/skill calls are not writes, so nothing in them is a path.
  - `file://` blanket-exempted, so a write-capable call naming a real target
    would slip through. Caught by: file:// resolves and IS judged on writes.
  - NotebookEdit carries `notebook_path`, not `file_path`, so every notebook
    write was silently exempt. Caught by: the field is read.

BROAD_ENFORCEMENT is re-enabled only while this suite is green.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOOKS = ROOT / "scripts/maintainer/hooks"
BAN = HOOKS / "md_file_ban_hook.py"
SHELL = HOOKS / "shell_file_creation_guard_hook.py"

if not BAN.is_file():
    pytest.skip("hook tree absent in this checkout", allow_module_level=True)

ALLOW, BLOCK = 0, 2

STRAY = "docs/plans/zz_host_probe.md"
LANE = "docs/agent/zz_host_probe.md"


def _run(script: Path, payload: dict) -> int:
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(ROOT)
    env.pop("Y9_PLACEMENT_BREADTH", None)
    return subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(payload), text=True, capture_output=True, env=env,
        cwd=str(ROOT), timeout=60,
    ).returncode


# --------------------------------------------------------------- Claude host

def test_write_payload_blocks_a_stray():
    assert _run(BAN, {"tool_name": "Write",
                      "tool_input": {"file_path": STRAY, "content": "x"}}) == BLOCK


def test_write_payload_allows_the_lane():
    assert _run(BAN, {"tool_name": "Write",
                      "tool_input": {"file_path": LANE, "content": "x"}}) == ALLOW


def test_edit_payload_shape():
    assert _run(BAN, {"tool_name": "Edit",
                      "tool_input": {"file_path": STRAY,
                                     "old_string": "a", "new_string": "b"}}) == BLOCK


def test_multiedit_payload_shape():
    assert _run(BAN, {"tool_name": "MultiEdit",
                      "tool_input": {"file_path": STRAY,
                                     "edits": [{"old_string": "a", "new_string": "b"}]}}) == BLOCK


def test_notebookedit_uses_notebook_path_not_file_path():
    """NotebookEdit names its target `notebook_path`. Reading only `file_path`
    meant every notebook write was exempt without anyone noticing."""
    assert _run(BAN, {"tool_name": "NotebookEdit",
                      "tool_input": {"notebook_path": STRAY,
                                     "new_source": "x"}}) == BLOCK


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"tool_name": "Read", "tool_input": {"file_path": "skill://doc-hygiene"}},
                     id="read-skill-uri"),
        pytest.param({"tool_name": "Read", "tool_input": {"file_path": STRAY}},
                     id="read-a-stray-path"),
        pytest.param({"tool_name": "Skill", "tool_input": {"skill": "doc-hygiene"}},
                     id="skill-invocation"),
        pytest.param({"tool_name": "Grep", "tool_input": {"pattern": "x", "path": STRAY}},
                     id="grep"),
        pytest.param({"tool_name": "Glob", "tool_input": {"pattern": "**/*.md"}}, id="glob"),
    ],
)
def test_non_write_calls_are_never_placement_questions(payload):
    """A call that cannot create a file has no placement question in it."""
    assert _run(BAN, payload) == ALLOW


# ------------------------------------------------------------------ file://

def test_file_uri_on_a_write_is_judged_not_exempt():
    """The hole the URI carve-out opened: file:// on a write names a real
    target. Exempting every scheme would wave it through."""
    assert _run(BAN, {"tool_name": "Write",
                      "tool_input": {"file_path": f"file://{ROOT}/{STRAY}"}}) == BLOCK


def test_file_uri_on_a_read_is_still_free():
    assert _run(BAN, {"tool_name": "Read",
                      "tool_input": {"file_path": f"file://{ROOT}/{STRAY}"}}) == ALLOW


@pytest.mark.parametrize("scheme", ["skill", "mcp", "https", "memory"])
def test_non_file_schemes_carry_no_target_even_on_writes(scheme):
    """They cannot be written to, so there is nothing to place."""
    assert _run(BAN, {"tool_name": "Write",
                      "tool_input": {"file_path": f"{scheme}://thing/name"}}) == ALLOW


# ---------------------------------------------------------------- Codex host

def test_codex_apply_patch_add_file():
    body = f"*** Begin Patch\n*** Add File: {STRAY}\n+x\n*** End Patch"
    assert _run(BAN, {"tool_name": "apply_patch", "tool_input": {"input": body}}) == BLOCK


def test_codex_apply_patch_into_the_lane():
    body = f"*** Begin Patch\n*** Add File: {LANE}\n+x\n*** End Patch"
    assert _run(BAN, {"tool_name": "apply_patch", "tool_input": {"input": body}}) == ALLOW


def test_codex_exec_command_cmd_key():
    assert _run(SHELL, {"tool_name": "exec_command",
                        "tool_input": {"cmd": f"printf x > {STRAY}"}}) == BLOCK


def test_codex_exec_command_argv_list():
    assert _run(SHELL, {"tool_name": "exec_command",
                        "tool_input": {"command": ["bash", "-lc", f"printf x > {STRAY}"]}}) == BLOCK


# ------------------------------------------------------------------ OMP host

def test_omp_bash_shape():
    assert _run(SHELL, {"tool_name": "bash",
                        "tool_input": {"command": f"printf x > {STRAY}"}}) == BLOCK


def test_omp_python_shape_is_presented_as_an_interpreter_call():
    code = "open('%s','w').write('x')" % STRAY
    assert _run(SHELL, {"tool_name": "bash",
                        "tool_input": {"command": f"python3 -c {json.dumps(code)}"}}) == BLOCK


def test_omp_write_shape_uses_path_field():
    assert _run(BAN, {"tool_name": "write_file", "tool_input": {"path": STRAY}}) == BLOCK


# ------------------------------------------------------- breadth is a gate

def test_broad_enforcement_is_on_while_this_suite_is_green():
    spec = importlib.util.spec_from_file_location(
        "dpp_breadth", HOOKS / "doc_placement_policy.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["dpp_breadth"] = mod
    spec.loader.exec_module(mod)
    assert mod.BROAD_ENFORCEMENT is True, (
        "broad enforcement was re-enabled on the strength of this suite; "
        "if it is off, the host-shaped gate has not been satisfied"
    )


# ------------------------------------------ outside the repository (FL-4853)
#
# The outside-repo rule moved from md_file_ban_hook into the policy module
# (is_forbidden_outside_repo). Both doors must give the same verdict, and the
# sample must hold the harness scratchpad next to the plain /tmp trap and a
# near-miss of the scratchpad shape.

OUTSIDE_SAMPLE = [
    ("/private/tmp/claude-501/-Users-r-Projects-asciicker-Y9-2/sess/scratchpad/raw_4.txt", ALLOW),
    ("/tmp/claude-501/-Users-r-Projects-asciicker-Y9-2/sess/scratchpad/sub/notes.md", ALLOW),
    ("/private/tmp/scratch/backup.md", BLOCK),
    ("/private/tmp/claude-501/loose_notes.md", BLOCK),
    ("/private/tmp/claude-501/a/b/not-scratchpad/raw.txt", BLOCK),
]


@pytest.mark.parametrize("path, verdict", OUTSIDE_SAMPLE)
def test_outside_repo_verdict_is_the_same_at_every_door(path, verdict):
    write = _run(BAN, {"tool_name": "Write", "tool_input": {"file_path": path, "content": "x"}})
    shell = _run(SHELL, {"tool_name": "Bash", "tool_input": {"command": f"ps aux > {path}"}})
    assert (write, shell) == (verdict, verdict), path

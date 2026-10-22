"""The shell is a second door into the same tree.

FL-4853: the Write tool refused `scripts/_hookprobe_positive.md` and
`.xx/AGENTS.md`; both were then created through Bash heredocs with no
resistance, because md_file_ban_hook.py gates on tool_name. These tests pin the
shell door shut for the forms an agent actually uses, and pin the read-only /
legitimate cases open so the guard stays usable.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "scripts/maintainer/hooks/shell_file_creation_guard_hook.py"

ALLOW, BLOCK = 0, 2


def _run(payload: dict) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(ROOT)
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload), text=True, capture_output=True, env=env, timeout=60,
    )


def bash(cmd: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": cmd}}


BLOCKED_COMMANDS = [
    pytest.param("cat > scripts/_p.md <<'EOF'\nx\nEOF", id="heredoc-the-observed-bypass"),
    pytest.param("mkdir -p .xx && cat > .xx/AGENTS.md <<'EOF'\nx\nEOF", id="hidden-dir-agents-md"),
    pytest.param("echo hi >stray.md", id="redirect-no-space"),
    pytest.param("echo hi >> notes.txt", id="append-txt"),
    pytest.param("echo hi | tee report.md", id="tee"),
    pytest.param("touch plan.txt", id="touch"),
    pytest.param("cp README.md docs/plans/copy.md", id="cp-destination"),
    pytest.param("python3 -c \"open('x.md','w').write('h')\"", id="inline-interpreter"),
    pytest.param("echo hi > .xx/notes.markdown", id="markdown-ext-redirect"),
]

ALLOWED_COMMANDS = [
    pytest.param("cat > docs/agent/ok.md <<'EOF'\nx\nEOF", id="lane-write"),
    pytest.param("echo hi > README.md", id="readme-exception"),
    pytest.param("git show HEAD:README.md > /dev/null", id="dev-null"),
    pytest.param("echo hi > /tmp/out.log", id="unbanned-extension"),
    pytest.param("grep -n foo docs/plans/GODOT_GOAL.MD", id="read-only"),
    pytest.param("rm -f stray.md", id="removing-a-stray-is-not-creating-one"),
    pytest.param("find . -maxdepth 4 -name X1 -o -name X2", id="find-or-is-not-output-flag"),
    pytest.param("grep -o PATTERN file.txt", id="grep-only-matching-takes-pattern"),
    pytest.param("echo x >> docs/proofs/README.md", id="appending-to-an-existing-doc"),
]


@pytest.mark.parametrize("cmd", BLOCKED_COMMANDS)
def test_forbidden_creation_is_blocked(cmd: str) -> None:
    proc = _run(bash(cmd))
    assert proc.returncode == BLOCK, proc.stderr
    assert "shell-file-guard" in proc.stderr


@pytest.mark.parametrize("cmd", ALLOWED_COMMANDS)
def test_legitimate_commands_pass(cmd: str) -> None:
    assert _run(bash(cmd)).returncode == ALLOW


def test_non_shell_tool_is_ignored() -> None:
    """The Write door has its own guard; this one must not double-block."""
    assert _run({"tool_name": "Read", "tool_input": {"file_path": "x.md"}}).returncode == ALLOW


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"tool_name": "exec_command", "tool_input": {"cmd": "echo x > stray.md"}},
                     id="codex-exec_command-cmd-key"),
        pytest.param({"tool_name": "exec_command",
                      "tool_input": {"command": ["bash", "-lc", "echo x > stray.md"]}},
                     id="codex-argv-list"),
        pytest.param({"tool_name": "shell", "tool_input": {"command": "touch stray.md"}},
                     id="codex-shell-alias"),
    ],
)
def test_codex_payload_shapes_are_covered(payload: dict) -> None:
    """Codex dispatches exec_command, not 'Bash' — the guard reads both."""
    assert _run(payload).returncode == BLOCK


def test_policy_is_imported_not_reimplemented() -> None:
    """Parity by construction: one policy module, two doors."""
    source = HOOK.read_text()
    assert "import md_file_ban_hook" in source
    assert "_policy._is_allowed" in source


def test_scratch_doc_outside_the_repo_is_blocked_too() -> None:
    """Parity with the Write door: /tmp is named in the policy, not exempt."""
    assert _run(bash("cp README.md /private/tmp/scratch/backup.md")).returncode == BLOCK
    assert _run(bash("cp README.md /private/tmp/scratch/backup.bak")).returncode == ALLOW


def test_harness_scratchpad_allows_extensionless_and_txt_outputs() -> None:
    assert _run(bash(f"printf x > {SCRATCH}/raw_output")).returncode == ALLOW
    assert _run(bash(f"printf x > {SCRATCH}/raw_output.txt")).returncode == ALLOW


def test_arrow_in_prose_is_not_write_intent() -> None:
    """`WRITE_INTENT` used to match a bare `>` followed by a word, so any
    script that printed "-> exit" while also using `python3 -c` looked like a
    file write. Redirects are REDIRECT_RE's job; write intent means a write API.
    """
    cmd = 'python3 -c "import sys" && echo "  stray.md -> exit 0"'
    assert _run(bash(cmd)).returncode == ALLOW


def test_read_only_population_glob_is_not_a_write_target() -> None:
    """A scan may mention ``*.md`` while writing a structured checkpoint."""
    cmd = (
        "python3 -c \"from pathlib import Path; list(Path('.').rglob('*.md')); "
        "Path('.state/session.json').write_text('{}')\""
    )
    assert _run(bash(cmd)).returncode == ALLOW


def test_interpreter_write_is_still_caught() -> None:
    assert _run(bash("python3 -c \"open('x.md','w').write('h')\"")).returncode == BLOCK


# ------------------------------------------- FL-4853: shell lexer extraction
#
# Each operator-reported false positive (2026-09-24) sits next to the nearby
# trap that must stay refused. A sample holding only the allows would pass a
# guard that blocks nothing; one holding only the blocks would pass the guard
# that produced the false positives. Both halves are asserted.

SCRATCH = "/private/tmp/claude-501/-Users-r-Projects-asciicker-Y9-2/sess/scratchpad"
EXISTING_DOC = "docs/proofs/README.md"

LEXER_PAIRS = [
    # 1. fd redirections are not paths
    pytest.param("python3 x.py --flag 2>&1 | tail -6",
                 "python3 x.py --flag 2>&1 > scripts/stray.txt", id="fd-dup-2>&1"),
    pytest.param("cp a.py b.py 2>/dev/null || cp a.py c.py",
                 "cp a.py b.py 2>/dev/null || cp a.md scripts/plan.md.bak", id="fd-redirect-in-cp-args"),
    pytest.param("python3 -m pip install foo 2>&1 | tail -6",
                 "python3 -m pip install foo 2>&1 | tee scripts/install_notes.txt", id="install-is-not-argv0"),
    # 2. -o option arguments are judged per command
    pytest.param("ps -o pid,ppid,lstart,command -p 30040",
                 "ps -o pid,command -p 1 > scripts/proc_notes.txt", id="ps-dash-o-option-list"),
    pytest.param("set -o pipefail; ls -la 2>&1",
                 "set -o pipefail; sort -o scripts/report.txt x.csv", id="set-dash-o-shell-option"),
    # 3. same-command variable assignments are resolved
    pytest.param(f"f={EXISTING_DOC} && cat >> $f <<'EOF'\nline\nEOF",
                 "f=scripts/new_notes.md && cat >> $f <<'EOF'\nline\nEOF", id="var-append-existing"),
    pytest.param("f=docs/agent/x-fl4853/brief.md; cat > \"$f\" <<'EOF'\nx\nEOF",
                 "f=.xx; cat > \"${f}/AGENTS.md\" <<'EOF'\nx\nEOF", id="var-quoted-and-braced"),
    # 4. writes outside the repository are not judged by repo placement
    pytest.param(f"S={SCRATCH}; python3 -c \"open('$S/raw_4.txt','w').write('x')\"",
                 "S=scripts; python3 -c \"open('$S/raw_4.txt','w').write('x')\"", id="var-in-interpreter"),
    pytest.param(f"ps aux > {SCRATCH}/raw_4.txt",
                 "ps aux > /private/tmp/scratch/raw_4.txt", id="harness-scratch-vs-plain-tmp"),
]


@pytest.mark.parametrize("allowed, blocked", LEXER_PAIRS)
def test_lexer_pairs_allow_the_ordinary_and_keep_the_trap(allowed: str, blocked: str) -> None:
    ok = _run(bash(allowed))
    assert ok.returncode == ALLOW, ok.stderr[:600]
    refused = _run(bash(blocked))
    assert refused.returncode == BLOCK, blocked
    assert "shell-file-guard" in refused.stderr


@pytest.mark.parametrize("cmd", [
    pytest.param("find . -name '*.py' -exec cp {} scripts/x.md \\;", id="find-exec-cp"),
    pytest.param("sudo cp a.py scripts/x.md", id="wrapped-cp"),
    pytest.param("bash -c 'echo x > scripts/stray.md'", id="bash-dash-c"),
    pytest.param("echo hi > .notes", id="invented-dotfile"),
    pytest.param("cat > ./scripts/_p.md <<'EOF'\nx\nEOF", id="dot-slash-prefix"),
])
def test_lexer_keeps_the_known_traps(cmd: str) -> None:
    assert _run(bash(cmd)).returncode == BLOCK


def test_unresolvable_target_fails_safe_with_a_clear_message() -> None:
    proc = _run(bash("cat > $UNSET_FL4853_VAR <<'EOF'\nx\nEOF"))
    assert proc.returncode == BLOCK
    assert "cannot tell where this command writes" in proc.stderr
    assert "$UNSET_FL4853_VAR" in proc.stderr


@pytest.mark.parametrize("cmd", [
    pytest.param('echo "a > b.md"', id="redirect-inside-quotes-is-text"),
    pytest.param("tmp=$(mktemp); echo x > \"$tmp\"", id="mktemp-variable"),
    pytest.param("echo x > \"$UNSET_FL4853_DIR/run.json\"", id="unresolved-dir-data-basename"),
    pytest.param("cat > docs/agent/x/t.py <<'PYEOF'\ndef f() -> None:\n    bash(\"cat > x <<'EOF'\\nx\\nEOF\")\nPYEOF",
                 id="heredoc-header-inside-another-body-is-data"),
])
def test_lexer_allows_non_creations(cmd: str) -> None:
    assert _run(bash(cmd)).returncode == ALLOW

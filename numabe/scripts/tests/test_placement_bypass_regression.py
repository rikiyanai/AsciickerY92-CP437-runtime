"""Every bypass class found in the 2026-09-19 enumeration audit, pinned.

Each test names the exact escape an agent used or could use. A test here must
fail if the hole reopens, so each one asserts on a payload shaped like the
host's, not on a helper's return value.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOOKS = os.path.join(ROOT, "scripts", "maintainer", "hooks")
sys.path.insert(0, HOOKS)

import doc_placement_policy as policy  # noqa: E402

PROSE = "# Task brief\n\nRead the failure log, then report what changed.\n"
YAML_DATA = "interface:\n  display_name: \"X\"\n  version: 3\n"


def _write_door(tool: str, tool_input: dict) -> int:
    proc = subprocess.run(
        [sys.executable, os.path.join(HOOKS, "md_file_ban_hook.py")],
        input=json.dumps({"tool_name": tool, "tool_input": tool_input}),
        capture_output=True, text=True, cwd=ROOT,
    )
    return proc.returncode


def _shell_door(command: str) -> int:
    proc = subprocess.run(
        [sys.executable, os.path.join(HOOKS, "shell_file_creation_guard_hook.py")],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}),
        capture_output=True, text=True, cwd=ROOT,
    )
    return proc.returncode


# ------------------------------------------------- extension shopping
@pytest.mark.parametrize("target", ["task.yaml", "task.yml", "task.json", "brief.csv"])
def test_prose_written_into_a_data_extension_is_refused(target):
    """The reported escape: refused on task.brief, renamed to task.yaml."""
    assert _write_door("Write", {"file_path": target, "content": PROSE}) == 2


@pytest.mark.parametrize("target,content", [
    ("config.yaml", YAML_DATA),
    ("data.json", '{"a": 1}'),
    (".vscode/launch.json", '// comment\n{"version": "0.2.0"}'),
    ("rows.csv", "a,b\n1,2\n"),
])
def test_real_data_in_a_data_extension_still_passes(target, content):
    assert _write_door("Write", {"file_path": target, "content": content}) == 0


def test_document_name_behind_a_freeform_suffix_is_refused():
    assert policy.is_forbidden_write("lane_brief.log", None) is True
    assert policy.is_forbidden_write("build_output.log", None) is False


# ------------------------------------------------- extension chain
@pytest.mark.parametrize("target", [
    "plan.md.bak", "notes.txt.old", "handoff.md.txt", "brief.md.log", "x.markdown.tmp",
])
def test_prose_suffix_anywhere_in_the_chain_counts(target):
    assert policy.is_forbidden_new_doc(target) is True


@pytest.mark.parametrize("target", ["world.a3d.backup", "main.py.orig", "level.tscn.bak2"])
def test_junk_suffix_on_a_real_artifact_still_passes(target):
    assert policy.is_forbidden_new_doc(target) is False


# ------------------------------------------------- dotfiles
@pytest.mark.parametrize("target", [".notes", ".plan", ".brief", ".handoff", ".taskbrief"])
def test_invented_dotfiles_are_prose(target):
    assert policy.is_forbidden_new_doc(target) is True


@pytest.mark.parametrize("target", [
    ".gitignore", ".clang-format", ".editorconfig", ".env", ".env.local",
    ".gitkeep", ".prettierrc", ".gitattributes",
])
def test_genuine_config_dotfiles_still_pass(target):
    assert policy.is_forbidden_new_doc(target) is False


# ------------------------------------------------- amnesty prefixes
@pytest.mark.parametrize("target", [
    "tmp/loose.md", "workspace/loose.md", "deploy/loose.md", "scripts/adhoc/loose.md",
])
def test_prefixes_with_no_tracked_prose_lost_their_amnesty(target):
    assert policy.is_forbidden_new_doc(target) is True


@pytest.mark.parametrize("target", [
    "artifacts/x.md", "articles/x.md", "docs/research/x.md",
    "godot_project/artifacts/maintainer/handoffs/INDEX.md",
    "colyseus_server/artifacts/maintainer/handoffs/session-123/SESSION_HANDOFF.md",
])
def test_producer_owned_trees_stay_exempt_at_any_depth(target):
    assert policy.is_forbidden_new_doc(target) is False


@pytest.mark.parametrize("target", [
    "artifacts/x/HANDOFF.md", "docs/audits/x-audit.md", ".run/x-status.md",
    "docs/research/x/verification/y/README.md",
])
def test_loose_evidence_notes_are_blocked_at_live_creation_doors(target):
    # Payloads exercise the real hooks without creating forbidden documents.
    assert _write_door("Write", {"file_path": target, "content": PROSE}) == 2
    patch = f"*** Begin Patch\n*** Add File: {target}\n+# Task note\n*** End Patch"
    assert _write_door("apply_patch", {"patch": patch}) == 2
    assert _shell_door(f"echo note > {target}") == 2


@pytest.mark.parametrize("target", [
    "artifacts/x/SESSION_HANDOFF.md",
    "artifacts/run_receipts/handoffs/SESSION_HANDOFF.md",
    "artifacts/maintainer/handoffs/SESSION_HANDOFF.md",
    "artifacts/maintainer/handoffs/session-123/status.md",
])
def test_named_producer_exception_does_not_open_neighbor_notes(target):
    assert policy.is_forbidden_new_doc(target)


# ------------------------------------------------- routers
@pytest.mark.parametrize("target", ["agents.md", "AGENTS.md", "CLAUDE.md", "README.md"])
def test_root_routers_stay_allowed(target):
    assert policy.is_forbidden_new_doc(target) is False


# ------------------------------------------------- shell door
def test_heredoc_fed_to_an_interpreter_is_scanned():
    """`python3 - <<PY` was the documented pre-check hole."""
    assert _shell_door("python3 - <<'PY'\nopen('loose.md','w').write('x')\nPY") == 2


def test_heredoc_fed_to_a_shell_keeps_its_redirect():
    assert _shell_door("bash <<'SH'\necho hi > loose.md\nSH") == 2


@pytest.mark.parametrize("body", [
    "from pathlib import Path\nPath('brief.md').write_text('x')",
    "import shutil\nshutil.copy('a.md','stray.md')",
])
def test_non_open_write_apis_in_a_heredoc_are_caught(body):
    assert _shell_door(f"python3 - <<'PY'\n{body}\nPY") == 2


def test_a_quoted_command_inside_a_python_body_is_not_a_false_positive():
    assert _shell_door("python3 - <<'PY'\nprint('run: cat > notes.md')\nPY") == 0


def test_allowed_destination_from_a_heredoc_still_passes():
    assert _shell_door(
        "python3 - <<'PY'\nopen('docs/agent/lane/ok.md','w').write('x')\nPY") == 0


# ------------------------------------------------- the message
def test_block_message_states_the_reason_for_the_ban():
    msg = policy.explain("task.yaml", policy.block_reason("task.yaml", PROSE))
    assert "WHY THIS BAN EXISTS" in msg
    assert "Loose documents are how work gets lost" in msg
    assert "docs/agent/<date>-<fl-id>-<topic>/" in msg
    assert "does not parse as yaml data" in msg
    # It must not claim renaming is impossible without saying how it is caught.
    assert "CONTENT" in msg


def test_block_reason_names_the_specific_rule():
    assert "does not parse" in policy.block_reason("t.yaml", PROSE)
    assert "extension chain" in policy.block_reason("plan.md.bak")
    assert "dotfile" in policy.block_reason(".brief")


# --------------------------------------------- canonical surfaces (2026-09-19)
#
# The policy allow-listed only docs/agent/ and therefore refused the
# repository's own authorities. It refused a READ of
# docs/plans/GODOT_GOAL.MD:1-250 outright, because the ":1-250" locator broke
# the existence check and "​.MD:1-250" parsed as an unknown suffix.

@pytest.mark.parametrize("target", [
    "docs/FAILURE_LOG.md",
    "docs/plans/GODOT_GOAL.MD",
    "docs/plans/2026-03-22-multiplayer-canonical-spec.md",
    "AGENTS.md",
    "CLAUDE.md",
    "README.md",
])
def test_canonical_planning_surfaces_are_allowed(target):
    assert policy.is_forbidden_new_doc(target) is False


@pytest.mark.parametrize("target", [
    "docs/plans/roadmap-v2.md",
    "docs/plans/steam-mvp-plan.md",
    "docs/plans/2026-09-13-recovered-agent-plans/RECOVERY.md",
])
def test_non_canonical_plans_stay_blocked(target):
    """docs/plans/ is NOT opened wholesale.

    The commit guard flags new roadmap-shaped markdown there on purpose, to
    stop the population of competing plan documents growing. A blanket prefix
    would reopen exactly that hole, so the two doors must agree.
    """
    assert policy.is_forbidden_new_doc(target) is True


def test_policy_canonical_set_matches_the_commit_guard():
    """Parity: the two doors read the same canonical list, lowercased."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "guard", os.path.join(ROOT, "scripts", "git_commit_policy_guard.py"))
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    assert {p.lower() for p in guard.CANONICAL_PLANNING_PATHS} == \
        set(policy.CANONICAL_PLANNING_PATHS)


@pytest.mark.parametrize("target,bare", [
    ("docs/plans/GODOT_GOAL.MD:1-250", "docs/plans/GODOT_GOAL.MD"),
    ("scripts/a.py:10", "scripts/a.py"),
    ("loose.md:1-9", "loose.md"),
])
def test_line_range_locators_are_stripped(target, bare):
    """'path:1-250' is a read locator, not a filename."""
    assert policy.strip_locator(target) == bare
    assert policy.is_forbidden_new_doc(target) == policy.is_forbidden_new_doc(bare)


# --------------------------------------------- yaml receipts are data
#
# 116 of 197 recent lane receipts failed yaml.safe_load purely because a long
# unquoted value contained ": ". They carry a fixed key schema and are data.
# Calling them prose would refuse legitimate work and tell the author to
# rename the file, which is the behaviour this policy exists to stop.

RECEIPT_WITH_UNQUOTED_COLON = (
    "outcome: STATIC-ADMISSION-933-RETURN\n"
    "highest_evidenced_stage: Executed\n"
    "starting_head: effe5b9eadcb7d582f35519ca1eb0295a2cdba90\n"
    "named_unknown: all preserved untouched (verified: owned diff touches only\n"
    "  the 8 listed files) and nothing else moved\n"
    "owned_paths:\n"
    "- godot_project/render/backends/perspective/ProbeStaticAdmission.gd\n"
)


def test_receipt_that_fails_strict_yaml_is_still_data():
    import yaml
    with pytest.raises(Exception):
        yaml.safe_load(RECEIPT_WITH_UNQUOTED_COLON)
    assert policy.content_is_prose("x.yaml", RECEIPT_WITH_UNQUOTED_COLON) is False


def test_a_document_in_a_yaml_is_still_refused():
    assert policy.content_is_prose("x.yaml", PROSE) is True


@pytest.mark.parametrize("name,expected", [
    ("handoff.yaml", True),
    ("lane_brief.yaml", True),
    ("steps.yaml", False),
    ("order.yaml", False),
])
def test_bullet_list_ambiguity_is_broken_on_the_name(name, expected):
    """A markdown bullet list is also a valid YAML sequence.

    That case is genuinely ambiguous, so the name breaks the tie and only
    sentence-shaped items under a document name are refused.
    """
    bullets = ("- read the failure log\n"
               "- report back to the root\n"
               "- then stop working\n")
    assert policy.content_is_prose(name, bullets) is expected

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.maintainer import claim_guard as CLAIMS
from scripts.maintainer import install_hooks as INSTALL
from scripts.maintainer.lib import report_schema as REPORT_SCHEMA


WRAPPER = ROOT / "scripts" / "maintainer" / "hooks" / "project_scope_hook.py"


def test_every_installed_hook_uses_the_project_scope_owner() -> None:
    assert INSTALL.HOOKS == {}
    for hook in INSTALL.HOOKS.values():
        command = INSTALL.hook_command(hook)
        assert str(WRAPPER) in command
        assert str(hook["script"]).split()[0] in command


def test_legacy_y9_hook_command_is_scoped_without_touching_foreign_commands() -> None:
    legacy = f"python3 {ROOT}/scripts/maintainer/hooks/context_bloat_guard_hook.py --post"
    scoped = INSTALL.scope_existing_y9_command(legacy)
    assert str(WRAPPER) in scoped
    assert "context_bloat_guard_hook.py --post" in scoped
    foreign = "python3 /another/repository/hook.py"
    assert INSTALL.scope_existing_y9_command(foreign) == foreign


def test_reconcile_has_no_global_managed_hook_catalog() -> None:
    hooks = {
        "PreToolUse": [
            {
                "matcher": "Write",
                "hooks": [{"type": "command", "command": "python3 /unrelated/image_guard.py"}],
            },
        ],
    }

    assert INSTALL.managed_registration_surplus(hooks) == 0
    assert INSTALL.reconcile_managed_registrations(hooks) == 0
    assert INSTALL.managed_registration_surplus(hooks) == 0
    commands = [
        hook["command"]
        for group in hooks["PreToolUse"]
        for hook in group["hooks"]
    ]
    assert "python3 /unrelated/image_guard.py" in commands


def test_project_scope_wrapper_ignores_foreign_repository_payload(tmp_path: Path) -> None:
    payload = json.dumps(
        {"cwd": str(tmp_path), "tool_name": "Bash", "tool_input": {"command": "x"}}
    )
    result = subprocess.run(
        [sys.executable, str(WRAPPER), "--", str(ROOT / "does-not-exist.py")],
        cwd=tmp_path,
        input=payload,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_project_scope_wrapper_preserves_project_hook_exit() -> None:
    target = ROOT / "scripts" / "maintainer" / "hooks" / "worktree_ban_hook.py"
    result = subprocess.run(
        [sys.executable, str(WRAPPER), "--", str(target)],
        cwd=ROOT,
        input=json.dumps(
            {"tool_name": "Bash", "tool_input": {"command": "git worktree add ../x x"}}
        ),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert '"blocked": true' in result.stdout
    assert "worktree-ban" in result.stderr


def test_codex_scope_exclusions_are_absent() -> None:
    assert INSTALL.verify_codex_scope_exclusions(verbose=False)
    config = json.loads((ROOT / ".codex" / "hooks.json").read_text(encoding="utf-8"))
    commands = [
        str(hook.get("command", ""))
        for groups in config["hooks"].values()
        for group in groups
        for hook in group.get("hooks", [])
    ]
    excluded = (
        "process_failure_pattern_guard_hook.py",
        "fl4260_user_action_guard.py",
        "fl4359_proof_sensitivity_hook.py",
        "godot_visual_grid_canon_hook.py",
    )
    assert not any(fragment in command for fragment in excluded for command in commands)


def test_codex_general_safeguards_are_present() -> None:
    config = json.loads((ROOT / ".codex" / "hooks.json").read_text(encoding="utf-8"))
    commands = []
    screen_matchers = []
    for hook_type, groups in config["hooks"].items():
        for group in groups:
            for hook in group.get("hooks", []):
                command = str(hook.get("command", ""))
                commands.append(command)
                if "screen_recording_command_guard_hook.py --hook" in command:
                    screen_matchers.append((hook_type, group.get("matcher")))
    assert any(command.endswith("worktree_ban_hook.py") for command in commands)
    assert any("adhoc_command_materializer_hook.py --host codex" in command for command in commands)
    # Codex matches the dispatched tool name against the matcher as a REGEX,
    # The name it sends was measured live as 'Bash' (2026-09-19). Pin
    # reachability against the measured name: an unmatched matcher means the
    # hook never runs while this test still passes (FL-4853).
    assert len(screen_matchers) == 1
    _event, _matcher = screen_matchers[0]
    assert _event == "PreToolUse"
    assert re.search(_matcher, "Bash"), f"{_matcher!r} cannot match Bash"
    assert any("context_bloat_guard_hook.py --host codex" in command for command in commands)
    assert any("context_bloat_guard_hook.py --post --host codex" in command for command in commands)


def test_claude_general_safeguards_are_present() -> None:
    config = json.loads(
        (ROOT / ".claude" / "settings.json").read_text(encoding="utf-8")
    )
    commands = [
        str(hook.get("command", ""))
        for groups in config["hooks"].values()
        for group in groups
        for hook in group.get("hooks", [])
    ]
    assert any(
        "adhoc_command_materializer_hook.py\" --host claude" in command
        for command in commands
    )


def test_repo_configs_omit_retired_lifecycle_entries() -> None:
    config = json.loads((ROOT / ".codex" / "hooks.json").read_text(encoding="utf-8"))
    commands = [
        str(hook.get("command", ""))
        for groups in config["hooks"].values()
        for group in groups
        for hook in group.get("hooks", [])
    ]
    for fragment in INSTALL._RETIRED_REPO_LOCAL_FRAGMENTS:
        assert not any(fragment in command for command in commands)


def test_global_policy_omits_project_specific_guard_entries() -> None:
    names = set(INSTALL.HOOKS)
    assert not {
        "fl4260_user_action_guard",
        "asciiid_computer_use_guard",
        "fl4588_html_user_action_guard",
        "fl4359_proof_sensitivity_guard",
        "godot_visual_grid_canon_write",
        "godot_visual_grid_canon_edit",
        "godot_visual_grid_canon_multiedit",
    } & names
    config = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    commands = [
        str(hook.get("command", ""))
        for groups in config["hooks"].values()
        for group in groups
        for hook in group.get("hooks", [])
    ]
    assert not any(
        fragment in command
        for fragment in INSTALL._CODEX_SCOPE_EXCLUDED_FRAGMENTS
        for command in commands
    )


def test_global_reconcile_removes_project_guards_only() -> None:
    hooks = {
        "PreToolUse": [{
            "matcher": "*",
            "hooks": [
                {"type": "command", "command": "python3 fl4260_user_action_guard.py --hook"},
                {"type": "command", "command": "python3 worktree_ban_hook.py"},
            ],
        }],
    }
    assert INSTALL.remove_scope_exclusions(hooks) == 1
    remaining = hooks["PreToolUse"][0]["hooks"]
    assert len(remaining) == 1
    assert "worktree_ban_hook.py" in remaining[0]["command"]


def test_context_guard_normalization_does_not_disable_images() -> None:
    command = (
        "python3 context_bloat_guard_hook.py --image-guard-disabled --host claude"
    )
    normalized = INSTALL.normalize_context_bloat_command(command)
    assert "--image-guard-disabled" not in normalized
    assert "--host claude" in normalized


def test_completion_stage_vocabulary_is_guarded() -> None:
    required = {
        "implemented", "connected", "executed", "verified", "accepted",
        "confirmed", "audited",
    }
    assert required <= REPORT_SCHEMA.FORBIDDEN_STATUS_WORDS


def test_fl_ticket_alone_does_not_prove_completion_claim() -> None:
    report = CLAIMS.check_message("FL-4780 verified and accepted", mode="block")
    assert any(f.category == "unsupported_claim" for f in report.findings)


def test_existing_commit_can_support_bounded_stage_claim() -> None:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    report = CLAIMS.check_message(
        f"FL-4780 implementation verified at commit {head}", mode="block"
    )
    assert not any(f.category == "unsupported_claim" for f in report.findings)

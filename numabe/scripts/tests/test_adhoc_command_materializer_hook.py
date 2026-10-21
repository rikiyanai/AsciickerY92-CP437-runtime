from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HOOK_PATH = ROOT / "scripts/maintainer/hooks/adhoc_command_materializer_hook.py"
OMP_EXTENSION = ROOT / ".omp/extensions/adhoc-command-materializer.ts"


def _load_hook():
    spec = importlib.util.spec_from_file_location("adhoc_materializer", HOOK_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


HOOK = _load_hook()


def _payload(command: str, root: Path) -> dict:
    return {
        "cwd": str(root),
        "tool_name": "Bash",
        "tool_input": {"command": command, "timeout": 30},
    }


def test_python_heredoc_is_saved_then_rewritten_to_the_saved_file(tmp_path: Path) -> None:
    command = "python3 - <<'PY'\nprint('kept')\nPY"
    call = HOOK.evaluate(_payload(command, tmp_path), root=tmp_path)

    assert call is not None
    assert call.kind == "python"
    assert call.path.parent == tmp_path / "scripts/adhoc"
    assert call.path.read_text(encoding="utf-8") == "print('kept')\n"
    assert call.updated_input["command"].startswith("python3 scripts/adhoc/")
    assert call.updated_input["timeout"] == 30
    executed = subprocess.run(
        call.updated_input["command"],
        cwd=tmp_path,
        shell=True,
        text=True,
        capture_output=True,
        check=False,
    )
    assert executed.returncode == 0
    assert executed.stdout == "kept\n"


def test_python_c_reuses_one_content_addressed_artifact(tmp_path: Path) -> None:
    payload = _payload("python3 -c \"print('same')\"", tmp_path)
    first = HOOK.evaluate(payload, root=tmp_path)
    second = HOOK.evaluate(payload, root=tmp_path)

    assert first is not None and second is not None
    assert first.path == second.path
    assert list((tmp_path / "scripts/adhoc").glob("*.py")) == [first.path]
    assert first.path.read_text(encoding="utf-8") == "print('same')"


def test_shell_program_is_saved_and_rewritten(tmp_path: Path) -> None:
    command = "for name in one two; do\n  printf '%s\\n' \"$name\"\ndone"
    call = HOOK.evaluate(_payload(command, tmp_path), root=tmp_path)

    assert call is not None
    assert call.kind == "shell"
    assert call.path.read_text(encoding="utf-8") == command
    assert call.updated_input["command"].startswith("zsh scripts/adhoc/")


def test_simple_shell_command_is_not_misclassified(tmp_path: Path) -> None:
    assert HOOK.evaluate(_payload("git status --short", tmp_path), root=tmp_path) is None
    assert not (tmp_path / "scripts/adhoc").exists()


def test_owned_adhoc_script_is_not_wrapped_again(tmp_path: Path) -> None:
    script = tmp_path / "scripts/adhoc/already.py"
    script.parent.mkdir(parents=True)
    script.write_text("print('owned')\n", encoding="utf-8")

    assert HOOK.evaluate(
        _payload("python3 scripts/adhoc/already.py", tmp_path), root=tmp_path
    ) is None


def test_python_c_argument_named_like_owned_script_is_still_materialized(
    tmp_path: Path,
) -> None:
    command = "python3 -c \"print('inline')\" scripts/adhoc/not-the-program.py"
    call = HOOK.evaluate(_payload(command, tmp_path), root=tmp_path)

    assert call is not None
    assert call.kind == "python"
    assert call.path.read_text(encoding="utf-8") == "print('inline')"


def test_root_adhoc_python_is_copied_before_execution(tmp_path: Path) -> None:
    source = tmp_path / "adhoc/legacy.py"
    source.parent.mkdir(parents=True)
    source.write_text("print('legacy')\n", encoding="utf-8")

    call = HOOK.evaluate(_payload("python3 adhoc/legacy.py --check", tmp_path), root=tmp_path)

    assert call is not None
    assert call.path.parent == tmp_path / "scripts/adhoc"
    assert call.path.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
    assert "scripts/adhoc/" not in call.updated_input["command"]
    assert "adhoc/legacy.py" in call.updated_input["command"]
    assert call.updated_input["command"].endswith("--check")


def test_ephemeral_script_executes_from_original_directory(tmp_path: Path) -> None:
    source = tmp_path / "adhoc/legacy.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "from pathlib import Path\n"
        "Path(__file__).with_name('producer.marker').write_text('source')\n",
        encoding="utf-8",
    )

    call = HOOK.evaluate(_payload("python3 adhoc/legacy.py", tmp_path), root=tmp_path)

    assert call is not None
    executed = subprocess.run(
        call.updated_input["command"],
        cwd=tmp_path,
        shell=True,
        text=True,
        capture_output=True,
        check=False,
    )
    assert executed.returncode == 0, executed.stderr
    assert (tmp_path / "adhoc/producer.marker").read_text() == "source"
    assert not (tmp_path / "scripts/adhoc/producer.marker").exists()


def test_omp_python_tool_is_saved_and_executes_the_saved_path(tmp_path: Path) -> None:
    payload = {
        "cwd": str(tmp_path),
        "tool_name": "python",
        "tool_input": {"code": "value = 6 * 7"},
    }
    call = HOOK.evaluate(payload, root=tmp_path)

    assert call is not None
    assert call.path.read_text(encoding="utf-8") == "value = 6 * 7"
    assert str(call.path) in call.updated_input["code"]


def test_codex_protocol_returns_updated_input_after_creation(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(HOOK_PATH), "--host", "codex", "--repo-root", str(tmp_path)],
        input=json.dumps(_payload("python3 -c \"print(42)\"", tmp_path)),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    response = json.loads(result.stdout)
    output = response["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    assert output["permissionDecision"] == "allow"
    assert "scripts/adhoc/" in output["updatedInput"]["command"]
    assert len(list((tmp_path / "scripts/adhoc").glob("*.py"))) == 1


def test_claude_protocol_rewrites_without_bypassing_permissions(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(HOOK_PATH),
            "--host",
            "claude",
            "--repo-root",
            str(tmp_path),
        ],
        input=json.dumps(_payload("python3 -c \"print(42)\"", tmp_path)),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    response = json.loads(result.stdout)
    output = response["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    assert "permissionDecision" not in output
    assert "scripts/adhoc/" in output["updatedInput"]["command"]
    assert len(list((tmp_path / "scripts/adhoc").glob("*.py"))) == 1


def test_omp_extension_delegates_to_shared_owner() -> None:
    text = OMP_EXTENSION.read_text(encoding="utf-8")
    assert 'pi.on("tool_call"' in text
    assert 'event.toolName !== "bash" && event.toolName !== "python"' in text
    assert "adhoc_command_materializer_hook.py" in text
    assert 'return { input: result.updatedInput }' in text


def test_codex_hook_registration_is_live() -> None:
    config = json.loads((ROOT / ".codex/hooks.json").read_text(encoding="utf-8"))
    matches = [
        (event, group.get("matcher"), hook.get("command", ""))
        for event, groups in config["hooks"].items()
        for group in groups
        for hook in group.get("hooks", [])
        if "adhoc_command_materializer_hook.py" in hook.get("command", "")
    ]
    assert len(matches) == 1
    event, matcher, command = matches[0]
    assert event == "PreToolUse"
    assert command == (
        f"python3 {ROOT}/scripts/maintainer/hooks/adhoc_command_materializer_hook.py --host codex"
    )
    # "live" has to mean reachable. Codex regex-matches the dispatched tool
    # name, measured live as 'Bash' (widened-matcher probe, 2026-09-19). Pin the
    # measured name, not an inferred one.
    assert re.search(matcher, "Bash"), f"{matcher!r} cannot match Bash"


def test_claude_hook_registration_is_live() -> None:
    config = json.loads((ROOT / ".claude/settings.json").read_text(encoding="utf-8"))
    matches = [
        (event, group.get("matcher"), hook.get("command", ""))
        for event, groups in config["hooks"].items()
        for group in groups
        for hook in group.get("hooks", [])
        if "adhoc_command_materializer_hook.py" in hook.get("command", "")
    ]
    assert matches == [
        (
            "PreToolUse",
            "Bash",
            'python3 "${CLAUDE_PROJECT_DIR:-.}/scripts/maintainer/hooks/'
            'adhoc_command_materializer_hook.py" --host claude',
        )
    ]

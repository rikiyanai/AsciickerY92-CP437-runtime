"""Installed helper payload regressions; staging parity remains explicit."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
STAGED = Path("/Users/r/.claude/task-observer/skill-updates/2026-09-30-reconciliation")
HELPER_PATH = Path('/Users/r/.claude/task-observer/hooks/task_observer_local.py')
GATE_PATH = Path('/Users/r/.claude/task-observer/hooks/shell_command_gate.py')
if not HELPER_PATH.is_file() or not GATE_PATH.is_file():
    pytest.skip("Installed-host integration suite: owned observer/shell helpers unavailable; not installation proof",
                allow_module_level=True)
spec = importlib.util.spec_from_file_location("task_observer_local", HELPER_PATH)
assert spec is not None and spec.loader is not None
observer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observer)
gate_spec = importlib.util.spec_from_file_location("shell_command_gate", GATE_PATH)
assert gate_spec is not None and gate_spec.loader is not None
gate = importlib.util.module_from_spec(gate_spec)
gate_spec.loader.exec_module(gate)


@pytest.mark.parametrize("command", [
    "python3 -c 'from subprocess import run as launch; launch([\"touch\", \"x\"])'",
    "python3 -c 'import subprocess; launch=subprocess.run; launch([\"touch\", \"x\"])'",
    "command eval 'touch x'",
])
def test_dynamic_execution_aliases_do_not_bypass_shell_boundary(command):
    assert gate.shell_write_reason(command)


def _observation(path: Path, *, identifier: str = "1", skill="[task-observer]") -> None:
    path.write_text(
        "---\n"
        f"id: {identifier}\n"
        "title: \"Fixture observation\"\n"
        "status: open\n"
        "type: open-source\n"
        f"skill: {skill}\n"
        "proposes_skill: []\n"
        "target_file: []\n"
        "siblings_checked: \"checked — no sibling family\"\n"
        "area: \"startup protocol\"\n"
        "date: 2026-09-30\n"
        "session_context: \"test\"\n"
        "parked_until:\n"
        "resolved:\n"
        "resolution:\n"
        "---\n\n"
        "**Issue:** A test issue.\n\n"
        "**Suggested improvement:** Make the gate structural.\n\n"
        "**Principle:** Evidence stays visible.\n",
        encoding="utf-8",
    )


def test_block_and_literal_yaml_scalars_are_selected_without_regex_fallback(tmp_path: Path) -> None:
    path = tmp_path / "0001-test.md"
    path.write_text(
        "---\n"
        "id: 1\n"
        "description: >\n"
        "  folded\n"
        "  description\n"
        "---\n",
        encoding="utf-8",
    )
    metadata, _ = observer._frontmatter(path.read_text())
    assert observer.folded_description(metadata) == "folded description"


def test_repo_citations_use_named_repo_form_and_normalize_dot_slash() -> None:
    assert observer.normalize_repo_citation("`./scripts/maintainer/x.py`") == "asciicker-Y9-2/scripts/maintainer/x.py"
    assert observer.normalize_repo_citation("asciicker-Y9-2/scripts/maintainer/x.py") == "asciicker-Y9-2/scripts/maintainer/x.py"


def test_per_skill_lookup_reads_only_matching_open_bodies(tmp_path: Path) -> None:
    log = tmp_path / "skill-observations/observation-log"
    log.mkdir(parents=True)
    _observation(log / "0001-good.md")
    _observation(log / "0002-other.md", identifier="2", skill="[other-skill]")
    result = observer.observations_for_skill(tmp_path, "task-observer")
    assert result["population"]["files"] == 2
    assert result["matching_open_count"] == 1
    assert "A test issue" in result["matches"][0]["body"]
    assert observer.observations_for_skill(tmp_path, "absent")["matching_open_count"] == 0


def test_per_skill_lookup_missing_mount_cannot_look_empty(tmp_path: Path) -> None:
    with pytest.raises(observer.ObserverValidationError):
        observer.observations_for_skill(tmp_path, "task-observer")


def test_malformed_id_0100_and_scalar_skill_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "0100-malformed.md"
    _observation(path, identifier="0100", skill="wayfinder-y9-fl")
    errors = observer.validate_observation(path)
    assert any("lexical form" in error for error in errors)
    assert any("skill must be a list" in error for error in errors)


def test_missing_body_headings_are_not_silently_population_valid(tmp_path: Path) -> None:
    path = tmp_path / "0100-missing-body.md"
    _observation(path, identifier="100")
    text = path.read_text(encoding="utf-8")
    path.write_text(text.split("**Issue:**", 1)[0], encoding="utf-8")
    errors = observer.validate_observation(path)
    assert errors
    assert any("body missing" in error for error in errors)


def test_canonical_unquoted_resolved_date_is_not_a_string_defect(tmp_path: Path) -> None:
    path = tmp_path / "0001-actioned.md"
    _observation(path)
    text = path.read_text().replace("status: open", "status: actioned").replace("resolved:\n", "resolved: 2026-09-30\n")
    path.write_text(text, encoding="utf-8")
    assert observer.validate_observation(path) == []
    path.write_text(text.replace("2026-09-30", "not-a-date"), encoding="utf-8")
    errors = observer.validate_observation(path)
    assert "date must be an ISO calendar date" in errors
    assert "resolved must be an ISO calendar date" in errors


def test_population_probe_is_independent_and_session_proof_is_real(tmp_path: Path) -> None:
    log = tmp_path / "skill-observations/observation-log"
    _observation(log / "0001-good.md") if log.exists() else None
    if not log.exists():
        log.mkdir(parents=True)
        _observation(log / "0001-good.md")
    (tmp_path / "skill-observations/cross-cutting-principles.md").write_text("Fixture principle", encoding="utf-8")
    (tmp_path / "skill-observations/last-review-date.txt").write_text("never\n", encoding="utf-8")
    result = observer.session_start(
        tmp_path,
        session_id="session-1",
        skill_root=tmp_path / "missing-skill",
        transcript_path=str(tmp_path / "transcript.jsonl"),
    )
    assert result["protocol_executed"] is True
    assert result["checkpoint_written"] is True
    assert result["skill_status"] == "not_installed"
    assert result["skill_lookup"]["exists"] is False
    assert (tmp_path / "skill-observations/checkpoints.log").is_file()
    assert result["review"]["offer_review"] is True
    assert result["hookSpecificOutput"]["hookEventName"] == "SessionStart"


def test_stop_uses_transcript_path_and_never_synthetic_transcript(tmp_path: Path) -> None:
    transcript = tmp_path / "real-transcript.jsonl"
    transcript.write_text(json.dumps({"type": "tool_use", "name": "Read", "path": "docs/agent/proof.md"}) + "\n", encoding="utf-8")
    result = observer.stop_backstop(
        tmp_path,
        {"transcript_path": str(transcript), "session_id": "missing-proof", "transcript": []},
    )
    assert result["tool_use_count"] == 1
    assert result["named_evidence"] == ["docs/agent/proof.md"]
    assert result["blocked"] is True
    assert result["transcript_path"] == str(transcript)
    active = observer.stop_backstop(
        tmp_path,
        {"transcript_path": str(transcript), "session_id": "missing-proof", "stop_hook_active": True},
    )
    assert active["blocked"] is False


def test_new_user_message_reselects_by_artefact_without_activation_claim() -> None:
    result = observer.reselect_for_message({"message": "inspect the placement hook", "artefact": "doc_placement_policy.py"})
    assert result["reselect_required"] is True
    assert result["artefact"] == "doc_placement_policy.py"
    assert result["skill_status"] == "fresh_session_unverified"
    assert "DECISION" in result["hookSpecificOutput"]["additionalContext"]


def test_nested_host_transcript_and_stop_cli_block(tmp_path: Path) -> None:
    transcript = tmp_path / "host.jsonl"
    transcript.write_text(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Reading"}, {"type": "tool_use", "id": "tool-1", "name": "Read", "input": {"file_path": "proof.md"}}
    ]}}) + "\n", encoding="utf-8")
    payload = {"session_id": "host-1", "transcript_path": str(transcript), "stop_hook_active": False}
    result = observer.stop_backstop(tmp_path, payload)
    assert result["tool_use_count"] == 1
    assert result["decision"] == "block"
    cli = subprocess.run([sys.executable, str(HELPER_PATH), "stop", "--workspace", str(tmp_path)],
                         input=json.dumps(payload), capture_output=True, text=True)
    assert cli.returncode == 0
    assert json.loads(cli.stdout)["decision"] == "block"


@pytest.mark.parametrize("contents", ["", "{broken", '{"invented":"shape"}\n'])
def test_unreadable_or_unparsed_transcript_is_not_a_clean_session(tmp_path: Path, contents: str) -> None:
    transcript = tmp_path / "host.jsonl"
    transcript.write_text(contents, encoding="utf-8")
    assert observer.stop_backstop(tmp_path, {"session_id": "host-1", "transcript_path": str(transcript)})["blocked"]
    assert observer.stop_backstop(tmp_path, {"session_id": "host-1", "transcript_path": str(tmp_path / "absent")})["blocked"]


def test_missing_mount_and_unsafe_session_id_refuse_startup(tmp_path: Path) -> None:
    with pytest.raises(observer.ObserverValidationError):
        observer.session_start(tmp_path, session_id="host-1", skill_root=tmp_path, transcript_path=str(tmp_path / "t"))
    for identifier in ("", "fresh-session", "../foreign", "/absolute"):
        with pytest.raises(observer.ObserverValidationError):
            observer.session_start(tmp_path, session_id=identifier, skill_root=tmp_path, transcript_path=str(tmp_path / "t"))


def test_other_session_or_transcript_proof_cannot_bless_stop(tmp_path: Path) -> None:
    log = tmp_path / "skill-observations/observation-log"
    log.mkdir(parents=True)
    _observation(log / "0001-good.md")
    (tmp_path / "skill-observations/cross-cutting-principles.md").write_text("Fixture principle", encoding="utf-8")
    (tmp_path / "skill-observations/last-review-date.txt").write_text("never\n", encoding="utf-8")
    transcript = tmp_path / "t.jsonl"
    transcript.write_text('{"type":"tool_use","name":"Read"}\n', encoding="utf-8")
    observer.session_start(tmp_path, session_id="host-1", skill_root=tmp_path, transcript_path=str(transcript))
    assert not observer.stop_backstop(tmp_path, {"session_id": "host-1", "transcript_path": str(transcript)})["blocked"]
    assert observer.stop_backstop(tmp_path, {"session_id": "host-2", "transcript_path": str(transcript)})["blocked"]
    other = tmp_path / "other.jsonl"
    other.write_text(transcript.read_text(), encoding="utf-8")
    assert observer.stop_backstop(tmp_path, {"session_id": "host-1", "transcript_path": str(other)})["blocked"]
    proof = tmp_path / ".state/sessions/host-1.json"
    data = json.loads(proof.read_text())
    data["protocol_executed"] = False
    proof.write_text(json.dumps(data), encoding="utf-8")
    assert observer.stop_backstop(tmp_path, {"session_id": "host-1", "transcript_path": str(transcript)})["blocked"]


def test_global_command_gate_rejects_output_redirection_but_ignores_quotes() -> None:
    assert gate.output_redirection("test > /dev/null") == ">"
    assert gate.output_redirection("test 2>&1") == ">&"
    assert gate.output_redirection("python3 -c \"print('a > b')\"") is None
    denied = gate.evaluate({"tool_name": "Bash", "tool_input": {"command": "test >> notes"}})
    assert denied is not None
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_long_session_revalidates_instead_of_expiring_honest_startup(tmp_path):
    log = tmp_path / "skill-observations/observation-log"
    log.mkdir(parents=True)
    _observation(log / "0001-good.md")
    (log.parent / "cross-cutting-principles.md").write_text("Fixture principle")
    (log.parent / "last-review-date.txt").write_text("never\n")
    transcript = tmp_path / "t.jsonl"
    transcript.write_text('{"type":"tool_use","name":"Read"}\n')
    observer.session_start(tmp_path, session_id="long-session", skill_root=tmp_path,
                           transcript_path=str(transcript))
    proof = tmp_path / ".state/sessions/long-session.json"
    data = json.loads(proof.read_text())
    data["timestamp"] = (observer._dt.datetime.now(observer._dt.timezone.utc)
                         - observer._dt.timedelta(days=3)).isoformat()
    proof.write_text(json.dumps(data))
    result = observer.stop_backstop(tmp_path, {"session_id": "long-session", "transcript_path": str(transcript)})
    assert not result["blocked"] and result["session_proof_refreshed"]
    assert json.loads(proof.read_text())["timestamp"] != data["timestamp"]
    # Refresh is not a waiver of malformed current population.
    proof.write_text(json.dumps(data))
    (log / "0001-good.md").write_text("not a header")
    result = observer.stop_backstop(tmp_path, {"session_id": "long-session", "transcript_path": str(transcript)})
    assert result["blocked"] and "revalidate with:" in result["reason"]


def test_pre_install_session_refusal_names_exact_recovery_without_fabricating_proof(tmp_path):
    transcript = tmp_path / "t's.jsonl"
    transcript.write_text('{"type":"tool_use","name":"Read"}\n')
    result = observer.stop_backstop(tmp_path, {"session_id": "older-session", "transcript_path": str(transcript)})
    assert result["blocked"] and "absent" in result["reason"]
    import shlex
    args = shlex.split(result["recovery_command"])
    assert args[args.index("--session-id") + 1] == "older-session"
    assert args[args.index("--transcript-path") + 1] == str(transcript)
    assert not (tmp_path / ".state/sessions/older-session.json").exists()


def test_proposed_host_configs_connect_start_stop_and_y9_shell_doors() -> None:
    for name in ("claude-settings.proposed.json", "codex-hooks.proposed.json", "y9-claude-settings.proposed.json"):
        config = json.loads((STAGED / "_config" / name).read_text(encoding="utf-8"))
        hooks = config["hooks"]
        assert any("task_observer_local.py" in json.dumps(entry) for entry in hooks["SessionStart"])
        assert any("task_observer_local.py" in json.dumps(entry) for entry in hooks["Stop"])
        assert any("message-reselect" in json.dumps(entry) for entry in hooks["UserPromptSubmit"])
    y9 = json.loads((STAGED / "_config/y9-claude-settings.proposed.json").read_text(encoding="utf-8"))
    assert "shell_file_creation_guard_hook.py" in json.dumps(y9["hooks"]["PreToolUse"])
    assert "adhoc_command_materializer_hook.py" in json.dumps(y9["hooks"]["PreToolUse"])
    assert "shell_command_gate.py" in json.dumps(y9["hooks"]["PreToolUse"])


@pytest.mark.parametrize('command', [
    "bash -lc 'printf x > notes'", 'sh -c "echo x >> notes"',
    'echo "$(printf x > notes)"', 'echo `printf x > notes`',
    "sed -i '' s/x/y/ notes", "perl -pi -e 's/x/y/' notes", 'dd if=input of=notes',
    "python3 -c \"open('notes', 'w').write('x')\"",
    "python3 -c \"from pathlib import Path; Path('notes').write_text('x')\"",
    "node -e \"require('fs').writeFileSync('notes','x')\"", 'tee notes',
    'env LANG=C tee notes', 'X=1 touch notes', 'command tee notes',
    "python3 -c \"writer=open; writer('notes','w')\"",
    "python3 -c \"import subprocess; subprocess.run(['sh','-c','touch notes'])\"",
    "python3 <<'PY'\nopen('notes','w').write('x')\nPY",
    "p=tee; $p notes",
])
def test_nested_and_alternate_shell_writes_are_refused(command):
    result = gate.evaluate({'tool_name': 'Bash', 'tool_input': {'command': command}})
    assert result and result['hookSpecificOutput']['permissionDecision'] == 'deny'


@pytest.mark.parametrize('command', [
    "rg 'a > b' source", "python3 -c \"print('a > b')\"",
    "python3 -c \"print(open('source').read())\"", "bash -lc 'printf x | tail -n 1'",
    "printf '%s' '$(echo > not-executed)'", 'cmp base/atlas.png new/atlas.png',
    "rg 'tee' notes", "python3 -c \"print('old'.replace('old','new'))\"",
])
def test_read_only_and_literal_code_examples_remain_allowed(command):
    assert gate.evaluate({'tool_name': 'Bash', 'tool_input': {'command': command}}) is None


def test_codex_native_function_call_is_not_a_no_tool_transcript(tmp_path):
    transcript = tmp_path / 'rollout.jsonl'
    transcript.write_text(json.dumps({'type': 'response_item', 'payload': {
        'type': 'function_call', 'name': 'exec_command', 'arguments': '{"cmd":"true"}'}}) + '\n')
    assert observer._transcript_facts(transcript)[0] == 1


def test_stop_cli_emits_only_supported_host_output_keys(tmp_path):
    cli = subprocess.run([sys.executable, str(HELPER_PATH), 'stop', '--workspace', str(tmp_path)],
                         input='{}', capture_output=True, text=True)
    assert cli.returncode == 0
    assert set(json.loads(cli.stdout)) == {'decision', 'reason'}


def test_startup_failure_uses_actual_host_stop_contract(tmp_path):
    cli = subprocess.run([sys.executable, str(HELPER_PATH), 'session-start',
        '--workspace', str(tmp_path), '--skill-root', str(tmp_path)],
        input='{}', capture_output=True, text=True)
    assert cli.returncode == 0
    output = json.loads(cli.stdout)
    assert output['continue'] is False
    assert 'Observer startup failed' in output['stopReason']


def test_installed_and_staged_helpers_are_exactly_equal():
    assert HELPER_PATH.read_bytes() == (STAGED / '_config/task_observer_local.py').read_bytes()
    assert GATE_PATH.read_bytes() == (STAGED / '_config/shell_command_gate.py').read_bytes()


def test_startup_never_launders_unknown_status_as_valid_closed_population(tmp_path):
    log = tmp_path / 'skill-observations/observation-log'
    log.mkdir(parents=True)
    file = log / '0001-bad-status.md'
    _observation(file)
    file.write_text(file.read_text().replace('status: open', 'status: unknown'))
    with pytest.raises(observer.ObserverValidationError, match='invalid observation status'):
        observer.session_start(tmp_path, session_id='fixture', skill_root=tmp_path,
                               transcript_path=str(tmp_path / 'transcript.jsonl'))

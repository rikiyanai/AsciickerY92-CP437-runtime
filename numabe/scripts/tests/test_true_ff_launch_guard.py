"""FL-4516: launch admission regression fixtures; no Godot or Metal launch."""
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from scripts import true_ff_launch_guard as guard
from scripts import fl4895_glsl_check as compiler


HOOK = Path(__file__).resolve().parents[1] / "maintainer/hooks/true_ff_direct_launch_guard_hook.py"
spec = importlib.util.spec_from_file_location("true_ff_hook", HOOK)
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


def crawl(**changes):
    return {"schema": "true_ff.text_flip_summary.v1", "frames": 3, "pairs": 2,
            "ink_chars_mean": 4.0, "red": 0, "yellow": 0, "invalid_frames": 0, **changes}


@pytest.mark.parametrize("summary", [
    {}, {"frames": 3}, None, [], crawl(frames=1, pairs=0), crawl(ink_chars_mean=0),
    crawl(ink_chars_mean=float("nan")), crawl(ink_chars_mean=float("inf")),
    crawl(pairs=0), crawl(pairs=3), crawl(red=1), crawl(yellow=1),
    crawl(invalid_frames=1), crawl(red=None), crawl(frames=True), crawl(pairs="2"),
    {k: v for k, v in crawl().items() if k != "invalid_frames"},
])
def test_invalid_crawl_fails_closed(summary):
    assert not guard._crawl_ok(summary)[0]


def test_real_nonempty_temporal_summary_is_eligible():
    assert guard._crawl_ok(crawl())[0]


@pytest.fixture
def roots(tmp_path, monkeypatch):
    project, server = tmp_path / "project", tmp_path / "server"
    project.mkdir()
    server.mkdir()
    (project / "project.godot").write_text("config_version=5\n")
    (server / "package.json").write_text('{}')
    owned = tmp_path / "guard"
    monkeypatch.setattr(guard, "GUARD_DIR", owned)
    monkeypatch.setattr(guard, "LEDGER", owned / "launches.jsonl")
    monkeypatch.setattr(guard, "feature_bank_gate", lambda root: (True, "fixture bank pass"))
    monkeypatch.setattr(guard, "offline_test_gate", lambda root: (True, "fixture offline pass"))
    monkeypatch.setattr(guard, "host_shader_contract_gate", lambda root: (True, "fixture contract pass"))
    monkeypatch.setattr(guard, "metal_gate", lambda root: (True, "fixture Metal pass"))
    monkeypatch.setattr(guard, "offline_frame_gate", lambda root: (True, "fixture offline frame pass"))
    monkeypatch.setattr(guard, "_bundle_roots", lambda root: [])
    gaps = tmp_path / "launch_gaps.json"
    gaps.write_text(json.dumps({"schema": guard.GAPS_SCHEMA, "gaps": []}))
    monkeypatch.setattr(guard, "GAPS", gaps)
    # Fixture launches model an operator shell; agent ancestry has its own check.
    monkeypatch.setattr(guard, "agent_ancestor", lambda pid=None: "")
    return project, server


def admit(tmp_path, roots, name="run", reason=""):
    return guard.admit("fixture", tmp_path / name, reason,
                       project_dir=roots[0], server_dir=roots[1])


def evidence(run, rc=0):
    (run / "godot_client.log").write_text("normal fixture log\n")
    (run / "analysis_summary.json").write_text(json.dumps({
        "godot_rc": rc, "true_ff_text_flips": {"run": {"rc": 0, "summary": crawl()}}}))
    guard.record_completion(run, rc)


def test_failed_pending_launch_requires_reason(tmp_path, roots):
    assert admit(tmp_path, roots) == 0
    assert admit(tmp_path, roots, "second") == 9
    assert admit(tmp_path, roots, "replicate", "test explicit repeat") == 0
    assert guard._last_record()["replicate_reason"] == "test explicit repeat"


def test_runtime_change_but_not_instrumentation_unlocks(tmp_path, roots):
    assert admit(tmp_path, roots) == 0
    (tmp_path / "measure_true_ff_text_flips.py").write_text("# instrumentation only")
    assert admit(tmp_path, roots, "instrumentation") == 9
    (roots[0] / "world.gd").write_text("# runtime change")
    assert admit(tmp_path, roots, "changed") == 0


def test_launch_path_change_unlocks_corrected_runtime_route(tmp_path, roots, monkeypatch):
    launcher = tmp_path / "headed_launcher.py"
    launcher.write_text("TIMEOUT = 180\n")
    monkeypatch.setattr(guard, "RUNTIME_LAUNCH_FILES", (launcher,))
    assert admit(tmp_path, roots) == 0
    launcher.write_text("TIMEOUT = 2400\n")
    assert admit(tmp_path, roots, "corrected-launcher") == 0


@pytest.mark.parametrize("rc", [137, 124, 1, None, False])
def test_clean_log_crawl_cannot_hide_bad_exit(tmp_path, roots, rc):
    assert admit(tmp_path, roots) == 0
    evidence(tmp_path / "run", rc)
    assert not guard.launch_outcome(guard._last_record())[0]


def test_completed_stable_run_and_following_admission(tmp_path, roots):
    assert admit(tmp_path, roots) == 0
    evidence(tmp_path / "run")
    assert guard.launch_outcome(guard._last_record()) == (True, [])
    assert admit(tmp_path, roots, "second") == 0


def test_missing_completion_is_not_pass(tmp_path, roots):
    assert admit(tmp_path, roots) == 0
    (tmp_path / "run/godot_client.log").write_text("clean")
    assert not guard.launch_outcome(guard._last_record())[0]


def test_runtime_change_during_metal_refuses(tmp_path, roots, monkeypatch):
    def compile_and_edit(project):
        (project / "world.gd").write_text("changed while compiling")
        return True, "fixture"
    monkeypatch.setattr(guard, "metal_gate", compile_and_edit)
    assert admit(tmp_path, roots) == 9
    assert guard._last_record() is None


def test_runtime_change_after_admission_and_completion(tmp_path, roots):
    assert admit(tmp_path, roots) == 0
    assert guard.before_spawn(tmp_path / "run", *roots) == 0
    (roots[0] / "world.gd").write_text("changed")
    assert guard.before_spawn(tmp_path / "run", *roots) == 9
    evidence(tmp_path / "run")
    assert not guard.launch_outcome(guard._last_record())[0]


def test_selected_roots_are_bound(tmp_path, roots):
    assert admit(tmp_path, roots) == 0
    assert guard.before_spawn(tmp_path / "run", roots[1], roots[0]) == 9


def test_failed_metal_cannot_be_overridden(tmp_path, roots, monkeypatch):
    monkeypatch.setattr(guard, "metal_gate", lambda root: (False, "fixture failure"))
    assert admit(tmp_path, roots, reason="intentional repeat") == 9


def test_offline_failure_stops_before_contract_or_metal(tmp_path, roots, monkeypatch):
    calls = []
    monkeypatch.setattr(guard, "offline_test_gate",
                        lambda root: (calls.append("offline") is None and False, "fixture failure"))
    monkeypatch.setattr(guard, "host_shader_contract_gate",
                        lambda root: (calls.append("contract") is None, "fixture"))
    monkeypatch.setattr(guard, "metal_gate",
                        lambda root: (calls.append("metal") is None, "fixture"))
    assert admit(tmp_path, roots) == 9
    assert calls == ["offline"]


def test_bank_failure_stops_every_later_gate(tmp_path, roots, monkeypatch):
    calls = []
    monkeypatch.setattr(guard, "feature_bank_gate",
                        lambda root: (calls.append("banks") is None and False, "fixture failure"))
    monkeypatch.setattr(guard, "offline_test_gate",
                        lambda root: (calls.append("offline") is None, "fixture"))
    monkeypatch.setattr(guard, "host_shader_contract_gate",
                        lambda root: (calls.append("contract") is None, "fixture"))
    monkeypatch.setattr(guard, "metal_gate",
                        lambda root: (calls.append("metal") is None, "fixture"))
    assert admit(tmp_path, roots) == 9
    assert calls == ["banks"]


@pytest.mark.parametrize("mode", ["FC", "CF", "CC", "FF_SHARED"])
def test_explicit_non_ff_launch_is_not_coupled_to_true_ff_gates(tmp_path, roots, monkeypatch, mode):
    calls = []
    monkeypatch.setattr(guard, "open_gaps", lambda root: calls.append("gaps"))
    monkeypatch.setattr(guard, "feature_bank_gate", lambda root: calls.append("banks"))
    monkeypatch.setattr(guard, "offline_test_gate", lambda root: calls.append("offline"))
    monkeypatch.setattr(guard, "host_shader_contract_gate", lambda root: calls.append("contract"))
    monkeypatch.setattr(guard, "metal_gate", lambda root: calls.append("metal"))
    monkeypatch.setattr(guard, "offline_frame_gate", lambda root: calls.append("frame"))

    run = tmp_path / "non-ff"
    assert guard.admit("fixture", run, project_dir=roots[0], server_dir=roots[1],
                       presentation_mode=mode) == 0
    assert calls == []
    receipt = json.loads((run / "guard_admission.json").read_text())
    assert receipt["presentation_mode"] == mode
    assert receipt["feature_bank_gate"] == f"not_applicable:presentation_mode={mode}"
    assert guard.before_spawn(run, *roots) == 0


def test_invalid_presentation_mode_fails_closed(tmp_path, roots):
    assert guard.admit("fixture", tmp_path / "invalid", project_dir=roots[0],
                       server_dir=roots[1], presentation_mode="native") == guard.REFUSED


def test_bank_gate_checks_complete_static_rigid_and_terrain_inventory(monkeypatch):
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "banks=1224 sources=1222 diffs=0\n", "")

    monkeypatch.setattr(guard.subprocess, "run", fake_run)
    ok, detail = guard.feature_bank_gate(guard.ROOT / "godot_project")
    assert ok and "diffs=0" in detail
    assert commands == [[
        guard.sys.executable, str(guard.FEATURE_BANK_COMPILER),
        "build", "--all", "--rigid", "--terrain-map",
        str(guard.FEATURE_BANK_TERRAIN_MAP), "--check",
    ]]


def test_offline_gate_excludes_non_admission_entrypoints(tmp_path, monkeypatch):
    captured = {"commands": []}

    def fake_run(command, **kwargs):
        captured["commands"].append(command)
        output = ("scripts/tests/test_true_ff_launch_guard.py::test_fixture\n"
                  if "--collect-only" in command else "")
        return type("Result", (), {"returncode": 0, "stdout": output, "stderr": ""})()

    monkeypatch.setattr(guard.subprocess, "run", fake_run)
    monkeypatch.setattr(guard, "OFFLINE_TEST_CACHE", tmp_path / "offline-test-cache")
    monkeypatch.setattr(guard, "offline_test_fingerprint", lambda project: "fixture-key")
    ok, _ = guard.offline_test_gate(guard.ROOT / "godot_project")
    assert ok
    assert len(captured["commands"]) == 2
    assert captured["commands"][0].count("-q") == 2
    assert captured["commands"][0][-2:] == ["-q", "--collect-only"]
    expression = captured["commands"][0][captured["commands"][0].index("-k") + 1]
    for name in guard.OFFLINE_TEST_EXCLUSIONS:
        assert f"not {name}" in expression
    assert "test_live_mapping_population_and_water_cards_are_required" in \
        guard.OFFLINE_TEST_EXCLUSIONS


def test_offline_gate_refuses_if_collection_contains_an_excluded_stage(tmp_path, monkeypatch):
    forbidden = guard.OFFLINE_TEST_EXCLUSIONS[0]

    def fake_run(command, **kwargs):
        output = f"scripts/tests/test_any.py::{forbidden}\n"
        return type("Result", (), {"returncode": 0, "stdout": output, "stderr": ""})()

    monkeypatch.setattr(guard.subprocess, "run", fake_run)
    monkeypatch.setattr(guard, "OFFLINE_TEST_CACHE", tmp_path / "offline-test-cache")
    monkeypatch.setattr(guard, "offline_test_fingerprint", lambda project: "fixture-key")
    ok, detail = guard.offline_test_gate(guard.ROOT / "godot_project")
    assert not ok
    assert "excluded_nodes=" in detail


def test_offline_gate_reuses_only_a_source_stable_pass(tmp_path, monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        output = ("scripts/tests/test_true_ff_launch_guard.py::test_fixture\n"
                  if "--collect-only" in command else "1 passed\n")
        return type("Result", (), {"returncode": 0, "stdout": output, "stderr": ""})()

    monkeypatch.setattr(guard.subprocess, "run", fake_run)
    monkeypatch.setattr(guard, "OFFLINE_TEST_CACHE", tmp_path / "offline-test-cache")
    monkeypatch.setattr(guard, "offline_test_fingerprint", lambda project: "stable-key")
    assert guard.offline_test_gate(guard.ROOT / "godot_project")[0]
    assert len(calls) == 2
    ok, detail = guard.offline_test_gate(guard.ROOT / "godot_project")
    assert ok and detail.startswith("cached rc=0")
    assert len(calls) == 2


def test_contract_failure_stops_before_metal(tmp_path, roots, monkeypatch):
    calls = []
    monkeypatch.setattr(guard, "offline_test_gate",
                        lambda root: (calls.append("offline") is None, "fixture"))
    monkeypatch.setattr(guard, "host_shader_contract_gate",
                        lambda root: (calls.append("contract") is None and False, "fixture failure"))
    monkeypatch.setattr(guard, "metal_gate",
                        lambda root: (calls.append("metal") is None, "fixture"))
    assert admit(tmp_path, roots) == 9
    assert calls == ["offline", "contract"]
    assert guard._last_record() is None


def test_legacy_ledger_does_not_masquerade_as_runtime_edit(tmp_path, roots):
    guard.GUARD_DIR.mkdir()
    guard.LEDGER.write_text(json.dumps({"fingerprint": "old-algorithm", "run_dir": str(tmp_path / "old")}) + "\n")
    assert admit(tmp_path, roots) == 9


def test_corrupt_ledger_fails_closed(tmp_path, roots):
    guard.GUARD_DIR.mkdir()
    guard.LEDGER.write_text("{broken\n")
    assert admit(tmp_path, roots) == 9


@pytest.mark.parametrize("command", [
    "godot --path godot_project", "env -u DISPLAY godot --path godot_project",
    "caffeinate -i godot --path godot_project", 'sh -c "godot --path godot_project"',
    "godot --path godot_project -- --help", "sudo -u r godot --path godot_project",
    "arch -arm64 godot --path godot_project", "FOO=1 /usr/bin/env -u DISPLAY godot",
    "echo safe && godot", "echo safe\ngodot", "(godot)",
    'open -a Godot --args --path godot_project', 'bash -lc "env -u DISPLAY godot"',
    "env -S 'godot --path godot_project'", "nohup /Applications/Godot.app/Contents/MacOS/Godot",
    "sudo --user r godot", "env --split-string='godot --path godot_project'",
    "open -a Godot --args -- --help",
])
def test_shell_direct_launch_blocked(command):
    assert hook.blocks_command(command)


@pytest.mark.parametrize("command", [
    "godot --headless --path godot_project", "godot --version", "godot --help",
    "env -u DISPLAY godot --headless", 'sh -c "godot --headless"',
    "python3 scripts/launch_fl4359_manual_play.py", "echo godot", "printf '%s' 'godot; --help'",
    "open -a Godot --args --headless --path godot_project",
])
def test_shell_nonlaunch_or_offline_allowed(command):
    assert not hook.blocks_command(command)


def test_hook_real_json_protocol():
    result = subprocess.run(["python3", str(HOOK)], input=json.dumps({
        "tool_name": "functions.exec_command", "tool_input": {"cmd": "caffeinate -i godot"}}),
        text=True, capture_output=True)
    assert result.returncode == 2
    assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_empty_shader_inventory_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(compiler, "PROJ", tmp_path)
    assert compiler.main(["--metal", "--all"]) == 2


def test_all_withdrawal_variants_are_compiled(tmp_path, monkeypatch):
    monkeypatch.setattr(compiler, "PROJ", tmp_path)
    source = '\n'.join('#ifdef TFF_WITHDRAWN_' + n + '\n#endif' for n in ("WATER", "SKY", "EFFECTS"))
    (tmp_path / "test.glsl").write_text("#[compute]\n#version 450\n" + source)
    checked = []
    monkeypatch.setattr(compiler, "metal_stage", lambda text, *args: (checked.append(text) is None, "", 0))
    assert len(compiler.metal_check("test.glsl", {})) == 8
    assert len(set(checked)) == 8
    assert any(all(f"#define TFF_WITHDRAWN_{name}" in s for name in ("WATER", "SKY", "EFFECTS")) for s in checked)


def test_family_split_wrappers_do_not_recompile_fixed_withdrawals():
    dynamic = "\n".join(f"#ifdef TFF_WITHDRAWN_{name}\n#endif" for name in ("WATER", "SKY", "EFFECTS"))
    assert len(compiler.family_defines(dynamic)) == 8
    fixed = "#define TFF_WITHDRAWN_SKY\n#define TFF_WITHDRAWN_EFFECTS\n" + dynamic
    assert compiler.family_defines(fixed) == [(), ("TFF_WITHDRAWN_WATER",)]
    all_fixed = "#define TFF_WITHDRAWN_WATER\n" + fixed
    assert compiler.family_defines(all_fixed) == [()]


def test_variant_cache_reuses_success_but_never_a_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(compiler, "VARIANT_CACHE", tmp_path / "variants")
    calls = []

    def fail_once(*args):
        calls.append("failure")
        return False, "TIMEOUT fixture", 16

    monkeypatch.setattr(compiler, "metal_stage", fail_once)
    tools = {"_identity": "fixture-toolchain"}
    assert not compiler.cached_metal_stage("void main() {}", "compute", tools, tmp_path)[0]
    assert not compiler.cached_metal_stage("void main() {}", "compute", tools, tmp_path)[0]
    assert calls == ["failure", "failure"]
    assert not list((tmp_path / "variants").glob("*.json"))

    def succeed(*args):
        calls.append("success")
        return True, "", 16

    monkeypatch.setattr(compiler, "metal_stage", succeed)
    assert compiler.cached_metal_stage("void main() {}", "compute", tools, tmp_path)[0]
    assert compiler.cached_metal_stage("void main() {}", "compute", tools, tmp_path)[0]
    assert calls == ["failure", "failure", "success"]


def test_explicit_project_overrides_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("GLSL_PROJ", "/wrong/tree")
    monkeypatch.setattr(compiler, "PROJ", Path("/wrong/tree"))
    assert compiler.main(["--project", str(tmp_path), "--metal", "--all"]) == 2
    assert compiler.PROJ == tmp_path


@pytest.mark.parametrize("report, rc", [
    ({"shaders": 0, "variants": 0, "failed": 0}, 0),
    ({"shaders": 1, "variants": 0, "failed": 0}, 0),
    ({"shaders": 1, "variants": 1, "failed": 1}, 0),
    ({"shaders": 1, "variants": 1, "failed": 0}, 1),
    (None, 0),
])
def test_metal_zero_exit_without_valid_inventory_is_rejected(tmp_path, monkeypatch, report, rc):
    monkeypatch.setattr(guard, "METAL_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(guard, "shader_fingerprint", lambda root: "fixture-key")
    monkeypatch.setattr(guard, "_true_ff_shader_inventory", lambda root: ["fixture.glsl"])
    stdout = "" if report is None else "METAL_GATE_RESULT " + json.dumps({"project": str(tmp_path), **report})
    monkeypatch.setattr(guard.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, rc, stdout, ""))
    assert not guard.metal_gate(tmp_path)[0]


def test_metal_cache_is_selected_tree_and_toolchain_keyed(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "METAL_CACHE_DIR", tmp_path / "cache")
    key = ["tools-A"]
    monkeypatch.setattr(guard, "shader_fingerprint", lambda root: key[0])
    monkeypatch.setattr(guard, "_true_ff_shader_inventory", lambda root: ["fixture.glsl"])
    calls = []
    def compile_fixture(command, **kwargs):
        calls.append(command)
        project = command[command.index("--project") + 1]
        return subprocess.CompletedProcess(command, 0, "METAL_GATE_RESULT " + json.dumps({
            "project": project, "shaders": 1, "variants": 8, "failed": 0}), "")
    monkeypatch.setattr(guard.subprocess, "run", compile_fixture)
    assert guard.metal_gate(tmp_path)[0]
    assert guard.metal_gate(tmp_path)[0]
    assert len(calls) == 1
    key[0] = "tools-B"
    assert guard.metal_gate(tmp_path)[0]
    assert len(calls) == 2
    assert guard.metal_gate(tmp_path / "replay")[0]
    assert len(calls) == 3


def test_metal_source_inventory_ignores_unrelated_gd_but_tracks_host_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "METAL_SHADER_HOST_FILES", (guard.METAL_SHADER_HOST_FILES[0],))
    project = tmp_path / "project"
    owner = project / guard.METAL_SHADER_HOST_FILES[0]
    shader = project / "render/ascii/features/example.glsl"
    unrelated = project / "menus/PauseMenu.gd"
    for path in (owner, shader, unrelated):
        path.parent.mkdir(parents=True, exist_ok=True)
    owner.write_text('const PROGRAM_PATH := "res://render/ascii/features/example.glsl"\n')
    shader.write_text("#[compute]\nvoid main() {}\n")
    unrelated.write_text("extends Control\n")

    selected = guard._shader_file_hashes(project)
    assert str(guard.METAL_SHADER_HOST_FILES[0]) in selected
    assert "render/ascii/features/example.glsl" in selected
    assert "menus/PauseMenu.gd" not in selected

    before = dict(selected)
    unrelated.write_text("extends Node\n")
    assert guard._changed_shader_files(project, before) == []

    owner.write_text('const PROGRAM_PATH := "res://render/ascii/features/changed.glsl"\n')
    assert guard._changed_shader_files(project, before) == [str(guard.METAL_SHADER_HOST_FILES[0])]


def test_metal_admission_collects_every_failure_in_one_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "METAL_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(guard, "shader_fingerprint", lambda root: "complete-metal-audit")
    monkeypatch.setattr(guard, "_true_ff_shader_inventory",
                        lambda root: ["render/ascii/features/required.glsl"])
    commands = []

    def compile_fixture(command, **kwargs):
        commands.append(command)
        project = command[command.index("--project") + 1]
        return subprocess.CompletedProcess(command, 0, "METAL_GATE_RESULT " + json.dumps({
            "project": project, "shaders": 2, "variants": 16, "failed": 0}), "")

    monkeypatch.setattr(guard.subprocess, "run", compile_fixture)
    assert guard.metal_gate(tmp_path)[0]
    assert len(commands) == 1
    assert "--metal" in commands[0]
    assert "--all" not in commands[0]
    assert "render/ascii/features/required.glsl" in commands[0]
    assert "--fail-fast" not in commands[0]


def test_offline_frame_fingerprint_ignores_unowned_runtime_files(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    owned = project / "owned.gd"
    bank = project / "bank"
    bank.mkdir()
    bank_file = bank / "current.bin"
    repository_owner = tmp_path / "offline.py"
    unrelated = project / "gameplay.gd"
    for path, content in ((owned, "owner"), (bank_file, "bank"),
                          (repository_owner, "offline"), (unrelated, "gameplay")):
        path.write_text(content)
    monkeypatch.setattr(guard, "OFFLINE_FRAME_PROJECT_FILES", (Path("owned.gd"),))
    monkeypatch.setattr(guard, "OFFLINE_FRAME_PROJECT_DIRS", (Path("bank"),))
    monkeypatch.setattr(guard, "OFFLINE_FRAME_REPO_FILES", (repository_owner,))
    monkeypatch.setattr(guard, "_shader_file_hashes", lambda root: {"shader.glsl": b"shader"})
    monkeypatch.setattr(guard, "_offline_frame_toolchain_identity", lambda: b"toolchain")

    before = guard.offline_frame_fingerprint(project)
    unrelated.write_text("changed gameplay")
    assert guard.offline_frame_fingerprint(project) == before
    owned.write_text("changed owner")
    assert guard.offline_frame_fingerprint(project) != before


def test_metal_source_changes_do_not_populate_success_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "METAL_CACHE_DIR", tmp_path / "cache")
    keys = iter(["before", "after"])
    monkeypatch.setattr(guard, "shader_fingerprint", lambda root: next(keys))
    monkeypatch.setattr(guard, "_true_ff_shader_inventory", lambda root: ["fixture.glsl"])
    monkeypatch.setattr(guard.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 0,
        "METAL_GATE_RESULT " + json.dumps({"project": str(tmp_path), "shaders": 1, "variants": 1, "failed": 0}), ""))
    assert not guard.metal_gate(tmp_path)[0]
    assert not (tmp_path / "cache/before.json").exists()


@pytest.mark.parametrize("filename, roots_text", [
    ("launch_fl4359_manual_play.py", "out_dir, GODOT_PROJECT, COLYSEUS_DIR"),
    ("run_fl4359_headed_movement.py", "out_dir, godot_project_dir, colyseus_dir"),
])
def test_launcher_has_admission_prespawn_and_completion(filename, roots_text):
    source = (Path(__file__).resolve().parents[1] / filename).read_text()
    admission = source.index("admission = _launch_guard.admit(")
    server = source.index("server = subprocess.Popen(", admission)
    recheck = source.index(f"_launch_guard.before_spawn({roots_text})", server)
    completion = source.index("_launch_guard.record_completion(", recheck)
    assert admission < server < recheck < completion
    assert "project_dir=" in source[admission:server]
    assert "server_dir=" in source[admission:server]
    compile(source, filename, "exec")

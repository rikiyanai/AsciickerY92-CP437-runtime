#!/usr/bin/env python3
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "launch_fl4359_manual_play.py"


def _load_launcher():
    spec = importlib.util.spec_from_file_location("fl4359_manual_launcher", LAUNCHER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stub_launch_source_identity(monkeypatch) -> None:
    """main() tests replace subprocess.Popen globally; the FL-4873 identity
    helper would then call the fake. Stub the helper, not the process fake."""
    import sys

    sys.path.insert(0, str(ROOT / "scripts" / "recording"))
    import f9_source_identity

    monkeypatch.setattr(
        f9_source_identity,
        "launch_source_identity",
        lambda captured_by: {"git_head": "test-head", "captured_by": captured_by},
    )


def test_manual_play_launcher_is_not_a_proof_wrapper() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")
    assert '"--colyseus-client"' in source
    assert "elif args.perspective:" in source
    assert 'cmd.append("--fl4270-force-perspective")' in source
    assert 'else:\n            cmd.append("--fl4270-force-perspective")' not in source
    assert "--script" not in source
    assert "fl4359_external_movement_driver.gd" not in source
    assert "--fl4359-jitter-capture" not in source
    assert "--fl4408-single-frame" not in source
    assert "analyze_fl4359_moving_camera.py" not in source


def test_manual_play_fullscreen_uses_world_presentation_owner() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")
    command_body = source.split("cmd = [GODOT_BIN]", 1)[1].split(
        'print("[fl4359-manual] launching human-playable Godot client"', 1
    )[0]
    engine_side, user_side = command_body.split('            "--",', 1)
    assert 'cmd.append("--windowed")' in engine_side
    assert '"--fullscreen"' not in engine_side
    assert 'cmd.append("--fl4359-force-fullscreen")' in user_side


def test_manual_play_ortho_is_an_explicit_camera_intent() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")
    assert 'if args.ortho:\n            cmd.append("--fl4270-force-ortho")' in source


def test_race_manual_launch_does_not_override_authoritative_grid_spawn() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")
    import ast
    tree = ast.parse(source)
    branches = [node for node in ast.walk(tree) if isinstance(node, ast.If)
                and ast.unparse(node.test) == "'--car-race' not in args.extra_user_arg"]
    assert len(branches) == 1
    branch = ast.unparse(branches[0])
    assert "env.setdefault('PLAYER_SPAWN_X', '97.0')" in branch
    assert "env.setdefault('PLAYER_SPAWN_Z', '0.0')" in branch


def test_manual_play_airborne_spawn_remains_server_owned() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")
    # This user-facing switch starts the server with one already-airborne
    # airplane. It must keep using server environment inputs rather than a
    # scripted camera, route latch, client transform, or proof-only driver.
    assert '"--airborne-airplane"' in source
    assert 'env["AIRPLANE_SPAWN_AIRBORNE"] = "1"' in source
    assert 'env.setdefault("AIRPLANE_SPAWN_ALTITUDE", "45")' in source
    assert 'env.setdefault("AIRPLANE_SPAWN_THROTTLE", "0.85")' in source
    assert 'env.pop("AIRPLANE_SPAWN_AIRBORNE", None)' in source
    assert "fl4359_external_movement_driver.gd" not in source


def test_manual_play_claims_keyboard_focus_for_the_godot_window() -> None:
    """The launcher must own macOS window activation, and prove it worked.

    FL-4359 manual-run defect: a terminal-spawned process does not become
    frontmost on macOS, so an operator's keys reached the terminal instead of
    the game and correct input handling looked broken. Popen (not run) is
    load-bearing here — run() blocks until exit, so no activation owner can
    exist alongside it.
    """
    source = (ROOT / "scripts" / "launch_fl4359_manual_play.py").read_text(encoding="utf-8")
    assert "def _activate_macos_window(" in source
    assert "child = subprocess.Popen(" in source
    assert "godot_rc = child.wait()" in source
    # Verification against the focus authority, not the setter's exit code.
    assert "whose frontmost is true" in source
    assert '"activated"' in source
    # The control arm for the focus A/B must stay available.
    assert '"--no-window-activation"' in source
    # A durable receipt, so an activation claim is never merely self-reported.
    assert 'window_activation.json' in source


def test_manual_onfoot_route_receipt_is_provenance_bound() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")
    assert 'manual_route_receipt.json' in source
    assert '"manual_onfoot_live_colyseus"' in source
    assert '"input_authority": "real_user_input_only"' in source
    assert '"private_input_mutation": False' in source
    assert '"completion_signal": "launcher_exit_and_godot_client_exit"' in source
    assert '"start_head": _git_head()' in source
    assert "start_source_state = _runtime_source_state(snapshot_root)" in source
    assert '"start_source_state": start_source_state' in source
    assert '"run_identity": run_identity' in source
    assert '"mode": camera_mode' in source
    assert 'route_receipt["end_time"]' in source
    assert 'route_receipt["end_head"]' in source
    assert 'route_receipt["end_source_state"] = _runtime_source_state(snapshot_root)' in source
    assert 'route_receipt["source_state_stable"]' in source


def test_runtime_source_state_identifies_dirty_product_sources(tmp_path: Path) -> None:
    launcher = _load_launcher()
    first = launcher._runtime_source_state(tmp_path / "snapshots")
    second = launcher._runtime_source_state(tmp_path / "snapshots")

    assert first["available"] is True
    assert first["head"] == launcher._git_head()
    assert first["sha256"] == second["sha256"]
    assert len(first["sha256"]) == 64
    assert isinstance(first["untracked_paths"], list)


def test_manual_route_receipt_reads_the_server_published_map_identity(tmp_path: Path) -> None:
    launcher = _load_launcher()
    log_path = tmp_path / "colyseus_server.log"
    log_path.write_text(
        "ASCIICKER_COLYSEUS_SPIKE_LISTENING port=2567\n"
        "COLYSEUS_ROOM_MAP_ID map_id=game_map_y8 terrain=/tmp/game_map_y8.a3d\n"
        "COLYSEUS_ROOM_MAP_ID map_id=sbu_carto_scale225_20260601 terrain=/tmp/sbu.a3d\n",
        encoding="utf-8",
    )
    assert launcher._authoritative_map_id_from_server_log(log_path) == "sbu_carto_scale225_20260601"
    assert launcher._authoritative_map_id_from_server_log(tmp_path / "missing.log") is None


def test_manual_launcher_selects_the_initial_scene_before_engine_startup() -> None:
    launcher = _load_launcher()
    assert launcher._launch_scene_for_user_args([]) == "res://scenes/Main.tscn"
    assert launcher._launch_scene_for_user_args(["--main-menu"]) == "res://scenes/ui/MainMenu.tscn"


def test_runtime_source_state_hashes_current_untracked_runtime_bytes(
    tmp_path: Path, monkeypatch
) -> None:
    launcher = _load_launcher()
    runtime_file = tmp_path / "godot_project" / "candidate.glsl"
    runtime_file.parent.mkdir(parents=True)
    runtime_file.write_bytes(b"first live source")

    class Completed:
        def __init__(self, stdout: bytes):
            self.stdout = stdout
            self.returncode = 0

    def fake_run(command, **kwargs):
        assert kwargs["cwd"] == tmp_path
        if command[1] == "diff":
            return Completed(b"tracked runtime diff")
        assert command[1:3] == ["ls-files", "--others"]
        return Completed(b"godot_project/candidate.glsl\0")

    monkeypatch.setattr(launcher, "ROOT", tmp_path)
    monkeypatch.setattr(launcher.subprocess, "run", fake_run)
    monkeypatch.setattr(launcher, "_git_head", lambda: "fixture-head")

    first = launcher._runtime_source_state(tmp_path / "snapshots")
    runtime_file.write_bytes(b"second live source")
    second = launcher._runtime_source_state(tmp_path / "snapshots")

    assert first["dirty"] is True
    assert first["untracked_paths"] == ["godot_project/candidate.glsl"]
    assert first["sha256"] != second["sha256"]


def test_detached_macos_engine_disappearance_is_an_unknown_exit(monkeypatch) -> None:
    launcher = _load_launcher()
    child = launcher._DetachedGodotProcess(4242)

    def missing_process(_pid: int, _signal: int) -> None:
        raise ProcessLookupError()

    monkeypatch.setattr(launcher.os, "kill", missing_process)

    assert child.poll() == launcher.DETACHED_GODOT_EXIT_STATUS_UNKNOWN
    assert child.wait(timeout=0) == launcher.DETACHED_GODOT_EXIT_STATUS_UNKNOWN
    assert child.exit_status_known is False
    assert child.exit_status_reason == "detached_engine_disappeared_exit_status_unknown"


def test_detached_process_never_fabricates_a_clean_exit() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")
    detached_body = source.split("class _DetachedGodotProcess:", 1)[1].split(
        "def _launch_macos_godot_bundle", 1
    )[0]
    assert "return 0" not in detached_body
    assert "DETACHED_GODOT_EXIT_STATUS_UNKNOWN" in detached_body


def test_macos_activation_targets_exact_registered_engine_pid(monkeypatch) -> None:
    launcher = _load_launcher()
    calls: list[list[str]] = []

    class Completed:
        returncode = 0
        stdout = "4242\n"
        stderr = ""

    def fake_run(command, **_kwargs):
        calls.append(command)
        return Completed()

    monkeypatch.setattr(launcher.sys, "platform", "darwin")
    monkeypatch.setattr(launcher, "_frontmost_process_name", lambda: "Ghostty")
    monkeypatch.setattr(launcher, "_frontmost_process_pid", lambda: 9999)
    monkeypatch.setattr(launcher, "_macos_process_registered", lambda pid: pid == 4242)
    monkeypatch.setattr(launcher.subprocess, "run", fake_run)
    receipt = launcher._activate_macos_window(4242, timeout=1.0)

    assert receipt["attempted"] is True
    assert receipt["activated"] is True
    assert receipt["target_pid"] == 4242
    assert receipt["target_selector"] == "System Events unix id 4242"
    assert receipt["registered"] is True
    assert receipt["frontmost_pid"] == 4242
    assert len(calls) == 2
    assert "unix id is 4242" in calls[0][-1]
    assert "open -a" not in " ".join(calls[0])
    assert "Godot" not in calls[0][-1]


def test_launcher_orders_exact_bundle_engine_before_activation_and_wait(
    tmp_path: Path, monkeypatch
) -> None:
    launcher = _load_launcher()
    # This suite owns launch/focus ordering, not guard subprocess execution.
    monkeypatch.setattr(launcher._launch_guard, "admit", lambda *_a, **_k: 0)
    monkeypatch.setattr(launcher._launch_guard, "before_spawn", lambda *_a, **_k: 0)
    monkeypatch.setattr(launcher._launch_guard, "record_completion", lambda *_a, **_k: None)
    events: list[tuple[str, object]] = []
    processes = iter((9100,))

    class FakeProcess:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def poll(self):
            return None

        def wait(self, **_kwargs):
            events.append(("wait", self.pid))
            return 0

        def send_signal(self, _signal):
            events.append(("signal", self.pid))

        def kill(self):
            events.append(("kill", self.pid))

    def fake_popen(command, **_kwargs):
        events.append(("popen", list(command)))
        return FakeProcess(next(processes))

    def fake_activate(pid: int):
        events.append(("activate", pid))
        return {
            "attempted": True,
            "activated": True,
            "reason": "godot_process_is_frontmost",
            "frontmost_before": "Ghostty",
            "frontmost_after": "godot",
        }

    def fake_bundle_launch(*_args, **_kwargs):
        events.append(("bundle_launch", 4242))
        return FakeProcess(4242), {
            "launch_method": "LaunchServices open -n Godot.app",
            "engine_pid": 4242,
            "launch_reason": "exact_engine_pid_resolved",
        }

    monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(launcher, "_port_open", lambda _port: False)
    monkeypatch.setattr(launcher, "_wait_for", lambda _predicate, _timeout: True)
    monkeypatch.setattr(launcher, "_launch_macos_godot_bundle", fake_bundle_launch)
    monkeypatch.setattr(launcher, "_activate_macos_window", fake_activate)
    monkeypatch.setattr(launcher, "_terminate", lambda _proc: None)
    monkeypatch.setattr(
        launcher,
        "_runtime_source_state",
        lambda _snapshot_root: {
            "available": True,
            "head": "test-head",
            "dirty": False,
            "sha256": "0" * 64,
            "snapshot": "artifacts/run_receipts/source_snapshots/" + "0" * 64 + "/snapshot.json",
        },
    )
    monkeypatch.setattr(launcher, "DURABLE_RECEIPT_ROOT", tmp_path / "durable")
    _stub_launch_source_identity(monkeypatch)
    monkeypatch.setattr(launcher, "GODOT_BIN", "/opt/homebrew/bin/godot")
    monkeypatch.setattr(launcher.sys, "argv", [
        str(LAUNCHER),
        "--out",
        str(tmp_path / "focus"),
        "--windowed",
        "--ortho",
    ])

    assert launcher.main() == 0
    assert events[0][0] == "popen"
    assert events[1] == ("bundle_launch", 4242)
    assert events[2] == ("activate", 4242)
    assert events[3] == ("wait", 4242)
    assert not any(event[0] == "popen" and event[1][0] == launcher.GODOT_BIN for event in events)


def test_bundle_launch_resolves_one_new_exact_engine_and_forwards_runtime_env(
    tmp_path: Path, monkeypatch
) -> None:
    launcher = _load_launcher()
    snapshots = iter((set(), {4242}))
    calls: list[list[str]] = []

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(launcher.sys, "platform", "darwin")
    monkeypatch.setattr(
        launcher,
        "_matching_godot_engine_pids",
        lambda _project, _port: set(next(snapshots)),
    )
    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda command, **_kwargs: calls.append(command) or Completed(),
    )
    child, receipt = launcher._launch_macos_godot_bundle(
        ["--path", str(launcher.GODOT_PROJECT), "--", "--port=2671"],
        {
            "COLYSEUS_PORT": "2671",
            "FL4359_INPUT_DIAG": "1",
            "SECRET_TOKEN": "must-not-leak",
        },
        tmp_path / "godot.log",
        launcher.GODOT_PROJECT,
        2671,
        False,
    )

    assert child is not None and child.pid == 4242
    assert receipt["engine_pid"] == 4242
    assert receipt["new_matching_pids"] == [4242]
    assert receipt["forwarded_environment_keys"] == ["COLYSEUS_PORT", "FL4359_INPUT_DIAG"]
    command = calls[0]
    assert command[:4] == ["/usr/bin/open", "-n", "-a", str(launcher.GODOT_APP)]
    assert "COLYSEUS_PORT=2671" in command
    assert "FL4359_INPUT_DIAG=1" in command
    assert all("SECRET_TOKEN" not in token for token in command)
    assert "--args" in command


def test_manual_play_strips_field_control_before_bundle_receipt(
    tmp_path: Path, monkeypatch
) -> None:
    launcher = _load_launcher()
    snapshots = iter((set(), {4242}))

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(
        launcher,
        "_matching_godot_engine_pids",
        lambda _project, _port: set(next(snapshots)),
    )
    monkeypatch.setattr(launcher.subprocess, "run", lambda *_args, **_kwargs: Completed())

    child_environment = launcher._manual_child_environment(
        {
            launcher.FL4216_WATER_INTERACTION_FIELD_CONTROL_OFF_ENV: "1",
            "FL4359_INPUT_DIAG": "1",
            "COLYSEUS_PORT": "2671",
        }
    )
    assert (
        launcher.FL4216_WATER_INTERACTION_FIELD_CONTROL_OFF_ENV
        not in child_environment
    )

    child, receipt = launcher._launch_macos_godot_bundle(
        ["--path", str(launcher.GODOT_PROJECT), "--", "--port=2671"],
        child_environment,
        tmp_path / "godot.log",
        launcher.GODOT_PROJECT,
        2671,
        False,
    )

    assert child is not None and child.pid == 4242
    assert (
        launcher.FL4216_WATER_INTERACTION_FIELD_CONTROL_OFF_ENV
        not in receipt["forwarded_environment_keys"]
    )
    assert receipt["forwarded_environment_keys"] == [
        "COLYSEUS_PORT",
        "FL4359_INPUT_DIAG",
    ]


def test_bundle_launch_rejects_multiple_new_matching_engines(tmp_path: Path, monkeypatch) -> None:
    launcher = _load_launcher()
    snapshots = iter((set(), {4242, 4243}))
    killed: list[tuple[int, int]] = []

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(
        launcher,
        "_matching_godot_engine_pids",
        lambda _project, _port: set(next(snapshots)),
    )
    monkeypatch.setattr(launcher.subprocess, "run", lambda *_args, **_kwargs: Completed())
    monkeypatch.setattr(launcher.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    child, receipt = launcher._launch_macos_godot_bundle(
        ["--path", str(launcher.GODOT_PROJECT), "--", "--port=2671"],
        {"COLYSEUS_PORT": "2671"},
        tmp_path / "godot.log",
        launcher.GODOT_PROJECT,
        2671,
        False,
    )

    assert child is None
    assert receipt["launch_reason"] == "multiple_matching_engine_pids"
    assert receipt["new_matching_pids"] == [4242, 4243]
    assert receipt["cleanup_pids"] == [4242, 4243]
    assert [pid for pid, _sig in killed] == [4242, 4243]


def test_bundle_launch_rejects_preexisting_exact_engine_without_open(
    tmp_path: Path, monkeypatch
) -> None:
    launcher = _load_launcher()

    def fail_open(*_args, **_kwargs):
        raise AssertionError("preexisting exact engine must stop before LaunchServices")

    monkeypatch.setattr(
        launcher,
        "_matching_godot_engine_pids",
        lambda _project, _port: {31337},
    )
    monkeypatch.setattr(launcher.subprocess, "run", fail_open)
    child, receipt = launcher._launch_macos_godot_bundle(
        ["--path", str(launcher.GODOT_PROJECT), "--", "--port=2671"],
        {"COLYSEUS_PORT": "2671"},
        tmp_path / "godot.log",
        launcher.GODOT_PROJECT,
        2671,
        False,
    )

    assert child is None
    assert receipt["launch_reason"] == "preexisting_matching_engine_pids"
    assert receipt["preexisting_matching_pids"] == [31337]
    assert receipt["open_returncode"] is None


def test_normal_macos_launch_fails_closed_when_focus_is_not_acquired(
    tmp_path: Path, monkeypatch
) -> None:
    launcher = _load_launcher()
    monkeypatch.setattr(launcher._launch_guard, "admit", lambda *_a, **_k: 0)
    monkeypatch.setattr(launcher._launch_guard, "before_spawn", lambda *_a, **_k: 0)
    monkeypatch.setattr(launcher._launch_guard, "record_completion", lambda *_a, **_k: None)
    events: list[tuple[str, int]] = []

    class FakeProcess:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def poll(self):
            return None

        def wait(self, **_kwargs):
            raise AssertionError("unfocused engine must not remain open")

    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess(9100))
    monkeypatch.setattr(launcher, "_port_open", lambda _port: False)
    monkeypatch.setattr(launcher, "_wait_for", lambda _predicate, _timeout: True)
    monkeypatch.setattr(
        launcher,
        "_launch_macos_godot_bundle",
        lambda *_args, **_kwargs: (
            FakeProcess(4242),
            {"launch_method": "bundle", "engine_pid": 4242, "launch_reason": "resolved"},
        ),
    )
    monkeypatch.setattr(
        launcher,
        "_activate_macos_window",
        lambda _pid: {
            "attempted": True,
            "activated": False,
            "reason": "frontmost_pid_mismatch",
            "frontmost_before": "Ghostty",
            "frontmost_after": "Ghostty",
        },
    )
    monkeypatch.setattr(launcher, "_terminate", lambda proc: events.append(("terminate", proc.pid)))
    monkeypatch.setattr(
        launcher,
        "_runtime_source_state",
        lambda _snapshot_root: {
            "available": True,
            "head": "test-head",
            "dirty": False,
            "sha256": "0" * 64,
            "snapshot": "artifacts/run_receipts/source_snapshots/" + "0" * 64 + "/snapshot.json",
        },
    )
    monkeypatch.setattr(launcher, "DURABLE_RECEIPT_ROOT", tmp_path / "durable")
    _stub_launch_source_identity(monkeypatch)
    monkeypatch.setattr(
        launcher.sys,
        "argv",
        [str(LAUNCHER), "--out", str(tmp_path / "fail-closed"), "--windowed"],
    )

    assert launcher.main() == 5
    assert events == [("terminate", 4242), ("terminate", 9100)]


def test_non_darwin_activation_is_explicit_noop(monkeypatch) -> None:
    launcher = _load_launcher()

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("non-Darwin activation must not invoke osascript")

    monkeypatch.setattr(launcher.sys, "platform", "linux")
    monkeypatch.setattr(launcher.subprocess, "run", fail_if_called)
    receipt = launcher._activate_macos_window(4242)

    assert receipt["attempted"] is False
    assert receipt["activated"] is False
    assert receipt["reason"] == "not_macos_no_activation_owner"
    assert receipt["target_pid"] == 4242


def test_macos_activation_failure_keeps_explicit_reason(monkeypatch) -> None:
    launcher = _load_launcher()

    class Failed:
        returncode = 1
        stdout = ""
        stderr = "not permitted"

    monkeypatch.setattr(launcher.sys, "platform", "darwin")
    monkeypatch.setattr(launcher, "_frontmost_process_name", lambda: "Ghostty")
    monkeypatch.setattr(launcher.subprocess, "run", lambda *_args, **_kwargs: Failed())
    monotonic_values = iter((0.0, 0.0, 2.0))
    monkeypatch.setattr(launcher.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(launcher.time, "sleep", lambda _seconds: None)
    receipt = launcher._activate_macos_window(4242, timeout=1.0)

    assert receipt["activated"] is False
    assert receipt["reason"] == "activation_command_failed_rc_1"
    assert receipt["activation_command_returncode"] == 1
    assert receipt["activation_command_stderr"] == "not permitted"

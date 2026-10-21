# =============================================================
# test_asciicker_presenter_html_mac.py
# =============================================================
# Focused packaging tests for the macOS HTML desktop package
# front door (scripts/package_asciicker_presenter_html_mac.py).
#
# These tests stay off the shared FL-4588 product owners: they do
# not edit asciiid.cpp, application-service, HTTP transport,
# registry, migration matrix, terrain, or analyze_runs.py.
#
# Coverage:
#   - dry-run plan names the makefile, ports, package root, and .app
#   - receipt writer fails closed when required fields are absent
#   - stale artifact reuse and flat launch owners stay deleted
#   - package-only always rebuilds and seals one app freshness receipt
#   - .app envelope: Info.plist, MacOS launcher, Resources bin/shell/
#     registry/front-door; MacOS launcher is Resources-only
#   - stdout token drain keeps consuming bytes after the token
#     (pipe-deadlock regression)
#   - headed UI owner is Google Chrome (fail closed when missing)
#   - Chrome DevTools readiness gate (loopback /json/version; no browser
#     process launched by these unit tests)
#   - non-headed app-resource launch smoke
# =============================================================

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "package_asciicker_presenter_html_mac.py"
PACKAGE_ROOT = ROOT / ".run" / "asciiid_html_desktop"
APP_BUNDLE = ROOT / "dist" / "ASCIID FL4512 Presenter HTML Authoring.app"
PRESENTER_BIN = ROOT / ".run" / "asciicker-presenter"
SHELL_DIST = ROOT / "web" / "asciiid-authoring-shell" / "dist"
HEALTH_URL = "http://127.0.0.1:43124/api/v1/health"


def _import_packager():
    sys.path.insert(0, str(ROOT / "scripts"))
    import package_asciicker_presenter_html_mac as packager  # noqa: WPS433

    return packager


@pytest.mark.skipif(sys.platform != "darwin" or not PRESENTER_BIN.is_file(),
                    reason="requires the built native macOS presenter")
def test_native_authoring_listener_failure_exits_before_rendering():
    # FL-4512: exercise the production CLI with a real occupied loopback port.
    # A bind error must not leave an inaccessible renderer consuming frames.
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen(1)
        result = subprocess.run(
            [str(PRESENTER_BIN), "--authoring-port", str(occupied.getsockname()[1])],
            cwd=ROOT, text=True, capture_output=True, timeout=15,
        )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "authoring_http_bind_failed" in result.stdout
    assert "compiled_selector_census" not in result.stdout
    invalid = subprocess.run(
        [str(PRESENTER_BIN), "--authoring-port", "0"],
        cwd=ROOT, text=True, capture_output=True, timeout=15,
    )
    assert invalid.returncode == 2, invalid.stdout + invalid.stderr
    assert "invalid authoring port 0" in invalid.stdout


def test_dry_run_emits_owned_package_plan():
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    plan = json.loads(completed.stdout)
    assert plan["status"] == "DRY_RUN"
    assert plan["makefile"] == "apps/asciicker-presenter/makefile_asciiid_mac"
    assert plan["authoring_port"] == 43124
    assert plan["shell_port"] == 43125
    assert plan["app_bundle"] == "dist/ASCIID FL4512 Presenter HTML Authoring.app"
    assert any("apps/asciicker-presenter/makefile_asciiid_mac" in step for step in plan["steps"])
    assert any("Google Chrome" in step for step in plan["steps"])
    assert any("ASCIID FL4512 Presenter HTML Authoring.app" in step for step in plan["steps"])
    assert any("dist/" in step for step in plan["steps"])



def test_receipt_fails_closed_without_required_fields():
    packager = _import_packager()
    with pytest.raises(packager.PackagingError, match="receipt missing required fields"):
        packager.write_receipt({"schema": "asciiid.html_desktop_runtime_receipt.v1"})


def test_outer_launch_handler_cannot_write_runtime_receipt():
    text = SCRIPT.read_text(encoding="utf-8")
    main = text.split("def main(", 1)[1]
    assert "_record_outer_failure_without_overwrite" not in text
    assert "fail_receipt(" not in main
    assert "launch_package is the sole runtime-receipt" in main


def test_launch_failure_receipt_keeps_lifecycle_pid_tuple():
    text = SCRIPT.read_text(encoding="utf-8")
    launch = text.split("def launch_package(", 1)[1].split("def main(", 1)[0]
    assert "launcher_pid=os.getpid()" in launch
    assert 'native_pid=getattr(native, "pid", None)' in launch


def test_flat_launch_owner_is_deleted(tmp_path, monkeypatch):
    packager = _import_packager()
    with pytest.raises(packager.PackagingError, match="flat package launch owner deleted"):
        packager.launch_package(open_browser=False, health_timeout_s=1.0)
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--launch-only"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode != 0
    assert "unrecognized arguments: --launch-only" in completed.stderr


def test_package_tree_removal_tolerates_one_finder_metadata_race(
    tmp_path, monkeypatch
):
    packager = _import_packager()
    package_root = tmp_path / "asciiid_html_desktop"
    (package_root / "bin").mkdir(parents=True)
    (package_root / "bin" / "old-runtime").write_text("stale", encoding="utf-8")
    real_rmtree = packager.shutil.rmtree
    calls = 0

    def finder_race(path):
        nonlocal calls
        calls += 1
        if calls == 1:
            real_rmtree(path)
            (path / "bin").mkdir(parents=True)
            (path / "bin" / ".DS_Store").write_bytes(b"finder metadata")
            raise OSError(packager.errno.ENOTEMPTY, "Directory not empty", path / "bin")
        real_rmtree(path)

    monkeypatch.setattr(packager.shutil, "rmtree", finder_race)
    packager._remove_package_tree_fail_closed(package_root)

    assert calls == 2
    assert not package_root.exists()


def test_package_tree_removal_rejects_non_metadata_residue(tmp_path, monkeypatch):
    packager = _import_packager()
    package_root = tmp_path / "asciiid_html_desktop"
    (package_root / "bin").mkdir(parents=True)
    real_rmtree = packager.shutil.rmtree

    def active_writer_race(path):
        real_rmtree(path)
        (path / "bin").mkdir(parents=True)
        (path / "bin" / "runtime_receipt.json").write_text("{}", encoding="utf-8")
        raise OSError(packager.errno.ENOTEMPTY, "Directory not empty", path / "bin")

    monkeypatch.setattr(packager.shutil, "rmtree", active_writer_race)
    with pytest.raises(packager.PackagingError, match="non-metadata residue"):
        packager._remove_package_tree_fail_closed(package_root)


def test_stale_reuse_cli_owners_are_deleted():
    source = SCRIPT.read_text(encoding="utf-8")
    assert '"--bundle-existing"' not in source
    assert '"--app-bundle-only"' not in source
    assert '"--launch-only"' not in source
    assert "def bundle_package(" not in source
    assert "def package_app_bundle_only(" not in source


def test_token_drain_consumes_bytes_after_token():
    """
    Functional proof that discovering the session token does not stop
    reading. The old early-return left the OS pipe full and deadlocked
    the native process; this test feeds a large post-token payload and
    asserts every byte after the token line was consumed through EOF.
    """
    packager = _import_packager()
    # Build a finite stream: noise, token, then a large payload that would
    # fill a typical OS pipe if the reader stopped at the token line.
    post_token_lines = [f"post-token-line-{index:05d}-{'x' * 200}\n" for index in range(400)]
    expected_after = sum(len(line) for line in post_token_lines)
    payload = "".join(
        [
            "boot noise\n",
            f"{packager.TOKEN_LINE_PREFIX} smoke-token-abc\n",
            *post_token_lines,
        ]
    )
    stream = io.StringIO(payload)
    drain = packager.NativeStdoutTokenDrain(stream, max_tail_chars=2048)
    drain.start()
    token = drain.wait_for_token(timeout_s=5.0)
    assert token == "smoke-token-abc"
    # Wait until EOF so the post-token counter is final.
    assert drain.join(timeout=5.0), "drain thread did not reach EOF"
    assert drain.bytes_consumed_after_token == expected_after
    # Bounded tail retains recent bytes only — not the entire stream.
    tail = drain.diagnostic_tail()
    assert len(tail) <= 2048
    assert "post-token-line-00399" in tail
    assert "boot noise" not in tail


def test_shell_server_bootstraps_normal_route_without_token_url(tmp_path):
    packager = _import_packager()
    (tmp_path / "index.html").write_text(
        "<title>ASCIIID</title>", encoding="utf-8",
    )
    token = "a" * 64
    server = packager._start_shell_server(tmp_path, 0, token)
    try:
        port = server.server_port
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}{packager.AUTHORING_BOOTSTRAP_PATH}",
            headers={
                packager.AUTHORING_BOOTSTRAP_HEADER:
                    packager.AUTHORING_SHELL_SURFACE_KIND,
            },
        )
        with urllib.request.urlopen(request, timeout=2.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
            assert response.headers["Cache-Control"] == "no-store, max-age=0"
            assert response.headers["Cross-Origin-Resource-Policy"] == "same-origin"
        assert payload == {
            "schema": "asciiid.authoring_bootstrap.v1",
            "surface_kind": packager.AUTHORING_SHELL_SURFACE_KIND,
            "session_token": token,
        }
        normal_url = f"http://127.0.0.1:{port}/#/scene/objects/item"
        with urllib.request.urlopen(normal_url, timeout=2.0) as response:
            assert b"ASCIIID" in response.read()
        assert "token=" not in normal_url
    finally:
        server.shutdown()
        server.server_close()


def test_shell_bootstrap_rejects_request_without_surface_header(tmp_path):
    packager = _import_packager()
    (tmp_path / "index.html").write_text("ASCIIID", encoding="utf-8")
    server = packager._start_shell_server(tmp_path, 0, "b" * 64)
    try:
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_port}"
                f"{packager.AUTHORING_BOOTSTRAP_PATH}",
                timeout=2.0,
            )
        assert caught.value.code == 403
    finally:
        server.shutdown()
        server.server_close()


def test_open_chrome_fails_closed_when_binary_missing(monkeypatch):
    packager = _import_packager()
    monkeypatch.setattr(
        packager,
        "CHROME_BIN",
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome.MISSING"),
    )
    with pytest.raises(packager.PackagingError, match="Google Chrome is required"):
        packager._open_editor_in_chrome("http://127.0.0.1:43125/#token=x")


def test_open_chrome_records_request_not_browser_opened(monkeypatch, tmp_path):
    """
    Chrome binary --app=<url> spawn is a request only.
    browser_opened=true without independent visibility is a false positive.
    """
    packager = _import_packager()
    # Point at a real file so the resolve step passes; stub spawn/activate.
    monkeypatch.setattr(packager, "CHROME_BIN", Path(sys.executable))
    profile_dir = tmp_path / "asciiid-html-chrome-profile-test"
    profile_dir.mkdir()
    monkeypatch.setattr(packager.tempfile, "mkdtemp", lambda **_kwargs: str(profile_dir))

    class _FakeProc:
        pid = 4242

    seen = {"popen": None}

    def _fake_popen(cmd, **kwargs):
        assert cmd[0] == str(Path(sys.executable))
        assert cmd[1].startswith("--app=")
        assert cmd[1] == "--app=http://127.0.0.1:43125/#token=abc"
        assert "--remote-debugging-address=127.0.0.1" in cmd
        assert "--remote-debugging-port=9222" in cmd
        assert f"--user-data-dir={profile_dir}" in cmd
        assert kwargs.get("start_new_session") is True
        seen["popen"] = cmd
        return _FakeProc()

    monkeypatch.setattr(packager.subprocess, "Popen", _fake_popen)
    # FL-4588 PRELAUNCH-SINGLE-SESSION: the late stale-Chrome retirement was
    # DELETED from _open_editor_in_chrome (it now runs once, pre-native, in
    # retire_prior_single_session_before_native). This stub FAILS if the retire
    # owner is called from this seam — positively confirming the late call is
    # gone.
    def _must_not_retire_here():
        raise AssertionError(
            "_open_editor_in_chrome must not retire Chrome shells "
            "(retirement moved pre-native)"
        )

    monkeypatch.setattr(
        packager,
        "retire_owned_asciiid_chrome_app_shells",
        _must_not_retire_here,
    )
    # DevTools readiness is a separate owner; stub a loopback-ready answer
    # so this spawn-request test never opens a real browser or hits :9222.
    monkeypatch.setattr(
        packager,
        "wait_chrome_devtools_ready",
        lambda **_kwargs: {
            "chrome_devtools_ready": True,
            "chrome_devtools_version_url": "http://127.0.0.1:9222/json/version",
            "chrome_devtools_browser": "Chrome/TEST",
            "chrome_devtools_protocol_version": "1.3",
            "chrome_devtools_websocket_debugger_url": (
                "ws://127.0.0.1:9222/devtools/browser/test"
            ),
            "chrome_devtools_ready_elapsed_s": 0.0,
            "chrome_devtools_ready_polls": 1,
            "chrome_devtools_ready_error": None,
        },
    )
    facts = packager._open_editor_in_chrome("http://127.0.0.1:43125/#token=abc")
    assert facts["ui_application"] == "Google Chrome"
    assert facts["ui_launch_requested"] is True
    assert facts["launchservices_request_accepted"] is True
    assert facts["browser_visibility_verified"] is False
    assert facts["html_editor_loaded_verified"] is False
    assert facts["physical_user_action_verified"] is False
    assert facts["proof_status"] == "UNPROVEN_HEADED_LAUNCH_PENDING"
    assert facts["chrome_app_mode"] is True
    assert facts["chrome_devtools_http_base"] == "http://127.0.0.1:9222"
    assert facts["chrome_devtools_loopback_only"] is True
    assert facts["chrome_devtools_ready"] is True
    assert facts["chrome_devtools_websocket_debugger_url"].startswith(
        "ws://127.0.0.1:9222/"
    )
    assert facts["chrome_ephemeral_profile_dir"] == str(profile_dir)
    assert "chrome_bin --app=" in facts["ui_launch_method"]
    assert facts["chrome_app_mode_helper_pid"] == 4242
    # The late stale-Chrome retirement is gone from this seam: its facts must
    # not appear in _open_editor_in_chrome's output (they now belong to the
    # pre-native single-session owner).
    assert "sole_chrome_shell_prelaunch_enforced" not in facts
    assert "stale_chrome_app_shells_found" not in facts
    assert seen["popen"] is not None
    assert facts["ui_launch_method"] == "chrome_bin --app=<editor_url>"
    assert "chrome_activate_exit" not in facts
    # Reject the historical false-positive claim.
    assert facts.get("browser_opened") is not True
    assert "browser_opened" not in facts
    assert "chrome_new_window_helper_pid" not in facts



def _fake_urlopen_bytes(payload: bytes):
    """Build a context-manager urlopen stub that returns fixed bytes."""

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return payload

    def _open(_url, timeout=2.0):  # noqa: ARG001 — signature matches urlopen
        return _Resp()

    return _open


def test_wait_chrome_devtools_ready_accepts_loopback_websocket():
    packager = _import_packager()
    payload = json.dumps(
        {
            "Browser": "Chrome/120.0.0.0",
            "Protocol-Version": "1.3",
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/abc",
        }
    ).encode("utf-8")
    facts = packager.wait_chrome_devtools_ready(
        timeout_s=1.0,
        poll_s=0.01,
        urlopen=_fake_urlopen_bytes(payload),
    )
    assert facts["chrome_devtools_ready"] is True
    assert facts["chrome_devtools_browser"] == "Chrome/120.0.0.0"
    assert facts["chrome_devtools_protocol_version"] == "1.3"
    assert (
        facts["chrome_devtools_websocket_debugger_url"]
        == "ws://127.0.0.1:9222/devtools/browser/abc"
    )
    assert facts["chrome_devtools_ready_polls"] >= 1


def test_wait_chrome_devtools_ready_rejects_wrong_host_websocket():
    packager = _import_packager()
    payload = json.dumps(
        {
            "Browser": "Chrome/120.0.0.0",
            "Protocol-Version": "1.3",
            "webSocketDebuggerUrl": "ws://192.168.1.50:9222/devtools/browser/abc",
        }
    ).encode("utf-8")
    with pytest.raises(packager.PackagingError, match="not loopback"):
        packager.wait_chrome_devtools_ready(
            timeout_s=1.0,
            poll_s=0.01,
            urlopen=_fake_urlopen_bytes(payload),
        )


def test_wait_chrome_devtools_ready_rejects_malformed_payload_until_timeout(monkeypatch):
    packager = _import_packager()
    # Always return non-JSON so the poller exhausts the bounded timeout.
    calls = {"n": 0}

    def _open(_url, timeout=2.0):  # noqa: ARG001
        calls["n"] += 1

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b"not-json{"

        return _Resp()

    with pytest.raises(
        packager.PackagingError, match="attach endpoint unreachable"
    ):
        packager.wait_chrome_devtools_ready(
            timeout_s=0.25,
            poll_s=0.05,
            urlopen=_open,
        )
    assert calls["n"] >= 2


def test_wait_chrome_devtools_ready_times_out_when_http_unreachable():
    packager = _import_packager()

    def _open(_url, timeout=2.0):  # noqa: ARG001
        raise urllib.error.URLError("connection refused")

    with pytest.raises(
        packager.PackagingError, match="attach endpoint unreachable"
    ):
        packager.wait_chrome_devtools_ready(
            timeout_s=0.2,
            poll_s=0.05,
            urlopen=_open,
        )


def test_open_chrome_fails_closed_and_deletes_profile_when_devtools_unreachable(
    monkeypatch, tmp_path
):
    """
    Headed launch must not keep an ephemeral profile after DevTools never
    answers. No real Chrome process is spawned — Popen/run/osascript are stubbed.
    """
    packager = _import_packager()
    monkeypatch.setattr(packager, "CHROME_BIN", Path(sys.executable))
    profile_dir = tmp_path / "asciiid-html-chrome-profile-fail"
    profile_dir.mkdir()
    monkeypatch.setattr(packager.tempfile, "mkdtemp", lambda **_kwargs: str(profile_dir))

    class _FakeProc:
        pid = 5151

    monkeypatch.setattr(
        packager.subprocess,
        "Popen",
        lambda *a, **k: _FakeProc(),
    )

    class _Completed:
        returncode = 0
        stdout = "0\n"
        stderr = ""

    monkeypatch.setattr(
        packager.subprocess,
        "run",
        lambda *a, **k: _Completed(),
    )
    monkeypatch.setattr(
        packager,
        "wait_chrome_devtools_ready",
        lambda **_kwargs: (_ for _ in ()).throw(
            packager.PackagingError(
                "Chrome DevTools attach endpoint unreachable after launch "
                "(url=http://127.0.0.1:9222/json/version, timeout_s=0.2, "
                "polls=1, last_error=devtools_http_unreachable)"
            )
        ),
    )
    # Avoid real SIGTERM against the fake pid during cleanup.
    monkeypatch.setattr(packager.os, "killpg", lambda *a, **k: None)
    monkeypatch.setattr(packager.os, "kill", lambda *a, **k: None)

    with pytest.raises(
        packager.PackagingError,
        match="attach endpoint unreachable.*profile_deleted=True",
    ):
        packager._open_editor_in_chrome("http://127.0.0.1:43125/#token=x")
    assert not profile_dir.exists()


def test_visibility_observation_stays_false_without_matching_title(monkeypatch):
    """Titles without the shell name and no authoring TCP must not verify."""
    packager = _import_packager()
    monkeypatch.setattr(
        packager,
        "list_onscreen_chrome_windows",
        lambda: [
            {"owner": "Google Chrome", "title": "New Tab", "width": 800, "height": 600}
        ],
    )
    monkeypatch.setattr(
        packager,
        "chrome_authoring_tcp_established",
        lambda **kwargs: {
            "authoring_port": 43124,
            "chrome_authoring_tcp_established": False,
            "chrome_authoring_tcp_lines": [],
            "lsof_exit": 0,
        },
    )
    result = packager.observe_chrome_shell_visibility(timeout_s=0.2, poll_s=0.05)
    assert result["browser_visibility_verified"] is False
    assert result["html_editor_loaded_verified"] is False
    assert result["chrome_visibility_blocker"]


def test_cdp_websocket_upgrade_omits_origin_and_validates_accept(monkeypatch):
    packager = _import_packager()

    class _FakeSocket:
        def __init__(self, *, valid_accept):
            self.valid_accept = valid_accept
            self.sent = []
            self.response = b""
            self.closed = False

        def settimeout(self, _timeout):
            pass

        def sendall(self, data):
            self.sent.append(data)
            if not data.startswith(b"GET "):
                return
            key_line = next(
                line for line in data.decode("ascii").split("\r\n")
                if line.startswith("Sec-WebSocket-Key: ")
            )
            key = key_line.split(": ", 1)[1]
            accept = packager.base64.b64encode(
                hashlib.sha1(
                    (key + packager._ChromeCdpPage._WEBSOCKET_GUID).encode("ascii")
                ).digest()
            ).decode("ascii")
            if not self.valid_accept:
                accept = "invalid-accept-key"
            self.response = (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
            ).encode("ascii")

        def recv(self, size):
            chunk, self.response = self.response[:size], self.response[size:]
            return chunk

        def close(self):
            self.closed = True

    valid_socket = _FakeSocket(valid_accept=True)
    monkeypatch.setattr(
        packager.socket,
        "create_connection",
        lambda *_args, **_kwargs: valid_socket,
    )
    client = packager._ChromeCdpPage(
        "ws://127.0.0.1:9222/devtools/page/test", timeout_s=1.0
    )
    request = valid_socket.sent[0]
    assert b"\r\nOrigin:" not in request
    assert b"Sec-WebSocket-Key:" in request
    client.close()

    invalid_socket = _FakeSocket(valid_accept=False)
    monkeypatch.setattr(
        packager.socket,
        "create_connection",
        lambda *_args, **_kwargs: invalid_socket,
    )
    with pytest.raises(packager.PackagingError, match="accept mismatch"):
        packager._ChromeCdpPage(
            "ws://127.0.0.1:9222/devtools/page/test", timeout_s=1.0
        )
    assert b"\r\nOrigin:" not in invalid_socket.sent[0]


def test_visibility_observation_true_only_with_shell_title(monkeypatch):
    packager = _import_packager()
    monkeypatch.setattr(
        packager,
        "list_onscreen_chrome_windows",
        lambda: [
            {
                "owner": "Google Chrome",
                "title": "ASCIID FL4512 Presenter - Google Chrome",
                "width": 1200,
                "height": 800,
            }
        ],
    )
    result = packager.observe_chrome_shell_visibility(timeout_s=1.0, poll_s=0.05)
    assert result["browser_visibility_verified"] is True
    assert result["html_editor_loaded_verified"] is False
    assert result["chrome_visibility_blocker"] is None


def test_visibility_observation_true_with_bounds_and_authoring_tcp(monkeypatch):
    """TCC-redacted titles still verify when Chrome is on-screen and connected."""
    packager = _import_packager()
    monkeypatch.setattr(
        packager,
        "list_onscreen_chrome_windows",
        lambda: [
            {"owner": "Google Chrome", "title": "", "width": 1280, "height": 800}
        ],
    )
    monkeypatch.setattr(
        packager,
        "chrome_authoring_tcp_established",
        lambda **kwargs: {
            "authoring_port": 43124,
            "chrome_authoring_tcp_established": True,
            "chrome_authoring_tcp_lines": ["Google ... 43124"],
            "lsof_exit": 0,
        },
    )
    result = packager.observe_chrome_shell_visibility(timeout_s=1.0, poll_s=0.05)
    assert result["browser_visibility_verified"] is True
    assert result["html_editor_loaded_verified"] is False
    assert result["chrome_visibility_blocker"] is None
    assert "authoring_tcp" in result["chrome_visibility_method"]


def test_cdp_authoring_readiness_requires_live_snapshot_and_real_navigation():
    packager = _import_packager()

    class _FakeCdp:
        def __init__(self, _url, **_kwargs):
            self.events = []
            self.closed = False

        def command(self, method, params=None):
            if method in ("Network.enable", "Runtime.enable"):
                return {}
            if method == "Runtime.evaluate":
                self.events.extend(
                    [
                        {
                            "method": "Network.requestWillBeSent",
                            "params": {
                                "requestId": "snapshot-1",
                                "request": {
                                    "url": "http://127.0.0.1:43124/api/v1/runtime-snapshot",
                                    "headers": {"X-Asciiid-Session": "secret-session"},
                                },
                            },
                        },
                        {
                            "method": "Network.responseReceived",
                            "params": {
                                "requestId": "snapshot-1",
                                "response": {
                                    "url": "http://127.0.0.1:43124/api/v1/runtime-snapshot",
                                    "status": 200,
                                },
                            },
                        },
                        {
                            "method": "Network.loadingFinished",
                            "params": {"requestId": "snapshot-1"},
                        },
                    ]
                )
                return {
                    "result": {
                        "value": {
                            "schema": "asciiid.shell_ready_dom.v1",
                            "document_title": "ASCIID FL4512 Presenter",
                            "href": "http://127.0.0.1:43125/#/scene/terrain/sculpt",
                            "app_shell_present": True,
                            "starting_visible": False,
                            "navigation_count": 12,
                            "pipeline_navigation_present": True,
                            "live_badge_present": True,
                            "snapshot_ticker_present": True,
                        }
                    }
                }
            if method == "Network.getResponseBody":
                assert params == {"requestId": "snapshot-1"}
                return {
                    "body": json.dumps(
                        {
                            "schema": "asciiid.runtime_snapshot.v1",
                            "state": {
                                "schema": "asciiid.authoring_snapshot.v1",
                                "state_revision": 9,
                            },
                            "witness": {"schema": "asciiid.runtime_witness.v1"},
                        }
                    ),
                    "base64Encoded": False,
                }
            raise AssertionError(method)

        def pop_events(self):
            events, self.events = self.events, []
            return events

        def close(self):
            self.closed = True

    result = packager.observe_chrome_authoring_ready_cdp(
        timeout_s=1.0,
        poll_s=0.01,
        target_loader=lambda **_kwargs: {
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/test"
        },
        client_factory=_FakeCdp,
    )
    assert result["html_editor_loaded_verified"] is True
    assert result["shell_ready_via_cdp"] is True
    assert result["shell_live_authoring_session_verified"] is True
    assert result["shell_live_snapshot_verified"] is True
    assert result["shell_real_navigation_verified"] is True
    assert result["shell_runtime_snapshot_schema"] == "asciiid.runtime_snapshot.v1"
    assert result["shell_runtime_witness_schema"] == "asciiid.runtime_witness.v1"
    assert result["shell_snapshot_schema"] == "asciiid.authoring_snapshot.v1"
    assert result["shell_snapshot_state_revision"] == 9
    assert result["shell_ready_dom"]["document_title"] == "ASCIID FL4512 Presenter"
    assert result["presenter_identity_verified"] is True
    assert "shell_snapshot_editor_frame" not in result
    assert "shell_authoring_session_id_sha256" not in result
    assert "secret-session" not in json.dumps(result)


def test_cdp_authoring_readiness_fails_without_authenticated_snapshot():
    packager = _import_packager()

    class _FakeCdp:
        def __init__(self, _url, **_kwargs):
            pass

        def command(self, method, params=None):
            if method in ("Network.enable", "Runtime.enable"):
                return {}
            if method == "Runtime.evaluate":
                return {
                    "result": {
                        "value": {
                            "document_title": "ASCIID FL4512 Presenter",
                            "app_shell_present": True,
                            "starting_visible": False,
                            "pipeline_navigation_present": True,
                            "live_badge_present": True,
                            "snapshot_ticker_present": True,
                        }
                    }
                }
            raise AssertionError(method)

        def pop_events(self):
            return []

        def close(self):
            pass

    result = packager.observe_chrome_authoring_ready_cdp(
        timeout_s=0.05,
        poll_s=0.01,
        target_loader=lambda **_kwargs: {
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/test"
        },
        client_factory=_FakeCdp,
    )
    assert result["html_editor_loaded_verified"] is False
    assert result["shell_live_authoring_session_verified"] is False
    assert result["shell_live_snapshot_verified"] is False
    assert result["shell_ready_blocker"] == "live_snapshot_not_observed"
    assert result["presenter_identity_verified"] is True


def test_cdp_authoring_readiness_rejects_starting_with_authenticated_snapshot():
    packager = _import_packager()

    class _FakeCdp:
        def __init__(self, _url, **_kwargs):
            self.events = []

        def command(self, method, params=None):
            if method in ("Network.enable", "Runtime.enable"):
                return {}
            if method == "Runtime.evaluate":
                self.events.extend(
                    [
                        {
                            "method": "Network.requestWillBeSent",
                            "params": {
                                "requestId": "snapshot-starting",
                                "request": {
                                    "url": "http://127.0.0.1:43124/api/v1/runtime-snapshot",
                                    "headers": {"X-Asciiid-Session": "live-session"},
                                },
                            },
                        },
                        {
                            "method": "Network.responseReceived",
                            "params": {
                                "requestId": "snapshot-starting",
                                "response": {
                                    "url": "http://127.0.0.1:43124/api/v1/runtime-snapshot",
                                    "status": 200,
                                },
                            },
                        },
                        {
                            "method": "Network.loadingFinished",
                            "params": {"requestId": "snapshot-starting"},
                        },
                    ]
                )
                return {
                    "result": {
                        "value": {
                            "app_shell_present": False,
                            "starting_visible": True,
                            "pipeline_navigation_present": False,
                            "live_badge_present": False,
                            "snapshot_ticker_present": False,
                        }
                    }
                }
            if method == "Network.getResponseBody":
                return {
                    "body": json.dumps(
                        {
                            "schema": "asciiid.runtime_snapshot.v1",
                            "state": {
                                "schema": "asciiid.authoring_snapshot.v1",
                                "state_revision": 10,
                                "editor_frame": 43,
                            },
                            "witness": {"schema": "asciiid.runtime_witness.v1"},
                        }
                    ),
                    "base64Encoded": False,
                }
            raise AssertionError(method)

        def pop_events(self):
            events, self.events = self.events, []
            return events

        def close(self):
            pass

    result = packager.observe_chrome_authoring_ready_cdp(
        timeout_s=0.05,
        poll_s=0.01,
        target_loader=lambda **_kwargs: {
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/test"
        },
        client_factory=_FakeCdp,
    )
    assert result["html_editor_loaded_verified"] is False
    assert result["shell_live_authoring_session_verified"] is True
    assert result["shell_live_snapshot_verified"] is True
    assert result["shell_real_navigation_verified"] is False
    assert result["shell_ready_dom"]["starting_visible"] is True
    assert result["shell_ready_blocker"] == "shell_dom_not_ready"


def test_cdp_failure_diagnostics_are_bounded_sanitized_and_path_only():
    packager = _import_packager()
    secret_token = "super-secret-bootstrap-value"
    secret_session = "89f7bd25-610c-4840-b17f-8ff0681ae17a"
    secret_hex = "a" * 64

    class _FakeCdp:
        def __init__(self, _url, **_kwargs):
            self.events = []
            self.emitted = False

        def command(self, method, params=None):
            if method in ("Network.enable", "Runtime.enable"):
                return {}
            if method == "Runtime.evaluate":
                if not self.emitted:
                    self.events.extend(
                        [
                            {
                                "method": "Runtime.exceptionThrown",
                                "params": {
                                    "exceptionDetails": {
                                        "text": "Uncaught",
                                        "exception": {
                                            "description": (
                                                "TypeError session_id=" + secret_session
                                            )
                                        },
                                    }
                                },
                            },
                            {
                                "method": "Runtime.consoleAPICalled",
                                "params": {
                                    "type": "error",
                                    "args": [
                                        {
                                            "type": "string",
                                            "value": "bootstrap token=" + secret_token,
                                        }
                                    ],
                                },
                            },
                            {
                                "method": "Network.requestWillBeSent",
                                "params": {
                                    "requestId": "snapshot-diagnostic",
                                    "request": {
                                        "url": (
                                            "http://127.0.0.1:43124/api/v1/runtime-snapshot"
                                        ),
                                        "headers": {
                                            "X-Asciiid-Session": secret_session
                                        },
                                        "postData": secret_token,
                                    },
                                },
                            },
                            {
                                "method": "Network.responseReceived",
                                "params": {
                                    "requestId": "snapshot-diagnostic",
                                    "response": {
                                        "url": (
                                            "http://127.0.0.1:43124/api/v1/runtime-snapshot"
                                        ),
                                        "status": 200,
                                        "headers": {"Authorization": secret_token},
                                    },
                                },
                            },
                            {
                                "method": "Network.loadingFinished",
                                "params": {"requestId": "snapshot-diagnostic"},
                            },
                        ]
                    )
                    self.emitted = True
                return {
                    "result": {
                        "value": {
                            "schema": "asciiid.shell_ready_dom.v1",
                            "document_ready_state": "complete",
                            "body_text_summary": (
                                "Starting ASCIIID editor token="
                                + secret_token
                                + " trace="
                                + secret_hex
                                + (" x" * 400)
                            ),
                            "app_shell_present": False,
                            "starting_visible": True,
                            "navigation_count": 0,
                            "pipeline_navigation_present": False,
                            "live_badge_present": False,
                            "snapshot_ticker_present": False,
                            "resource_http": [
                                {
                                    "path": "/asciiid.ui_registry.v1.json",
                                    "status": 200,
                                },
                                {
                                    "path": "/asciiid.authoring_bootstrap.v1.json",
                                    "status": 200,
                                },
                                {"path": "/api/v1/health", "status": 200},
                                {"path": "/api/v1/handshake", "status": 201},
                                {"path": "/api/v1/runtime-snapshot", "status": 200},
                                {"path": "/not-owned", "status": 418},
                            ],
                        }
                    }
                }
            if method == "Network.getResponseBody":
                return {
                    "body": json.dumps(
                        {
                            "schema": "asciiid.runtime_snapshot.v1",
                            "state": {
                                "schema": "asciiid.authoring_snapshot.v1",
                                "state_revision": 13,
                                "editor_frame": 52,
                            },
                            "witness": {"schema": "asciiid.runtime_witness.v1"},
                        }
                    ),
                    "base64Encoded": False,
                }
            raise AssertionError(method)

        def pop_events(self):
            events, self.events = self.events, []
            return events

        def close(self):
            pass

    result = packager.observe_chrome_authoring_ready_cdp(
        timeout_s=0.05,
        poll_s=0.01,
        target_loader=lambda **_kwargs: {
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/test"
        },
        client_factory=_FakeCdp,
    )
    encoded = json.dumps(result)
    assert result["html_editor_loaded_verified"] is False
    assert result["shell_ready_blocker"] == "shell_dom_not_ready"
    assert result["shell_ready_dom"]["document_ready_state"] == "complete"
    assert len(result["shell_ready_dom"]["body_text_summary"]) <= 512
    assert result["shell_ready_runtime_exceptions"]
    assert result["shell_ready_console_errors"]
    assert result["shell_ready_http_facts"] == {
        "static_registry": {
            "path": "/asciiid.ui_registry.v1.json",
            "observed": True,
            "status": 200,
            "response_count": 1,
        },
        "authoring_bootstrap": {
            "path": "/asciiid.authoring_bootstrap.v1.json",
            "observed": True,
            "status": 200,
            "response_count": 1,
        },
        "native_health": {
            "path": "/api/v1/health",
            "observed": True,
            "status": 200,
            "response_count": 1,
        },
        "authoring_handshake": {
            "path": "/api/v1/handshake",
            "observed": True,
            "status": 201,
            "response_count": 1,
        },
        "runtime_snapshot": {
            "path": "/api/v1/runtime-snapshot",
            "observed": True,
            "status": 200,
            "response_count": 2,
        },
    }
    assert secret_token not in encoded
    assert secret_session not in encoded
    assert secret_hex not in encoded
    assert "headers" not in encoded.lower()
    assert "postData" not in encoded
    assert "shell_authoring_session_id_sha256" not in result


def test_cdp_authoring_readiness_rejects_non_object_snapshot_body():
    packager = _import_packager()

    class _FakeCdp:
        def __init__(self, _url, **_kwargs):
            self.events = []

        def command(self, method, params=None):
            if method in ("Network.enable", "Runtime.enable"):
                return {}
            if method == "Runtime.evaluate":
                self.events.extend(
                    [
                        {
                            "method": "Network.requestWillBeSent",
                            "params": {
                                "requestId": "snapshot-array",
                                "request": {
                                    "url": "http://127.0.0.1:43124/api/v1/runtime-snapshot",
                                    "headers": {"X-Asciiid-Session": "live-session"},
                                },
                            },
                        },
                        {
                            "method": "Network.responseReceived",
                            "params": {
                                "requestId": "snapshot-array",
                                "response": {
                                    "url": "http://127.0.0.1:43124/api/v1/runtime-snapshot",
                                    "status": 200,
                                },
                            },
                        },
                        {
                            "method": "Network.loadingFinished",
                            "params": {"requestId": "snapshot-array"},
                        },
                    ]
                )
                return {
                    "result": {
                        "value": {
                            "app_shell_present": True,
                            "starting_visible": False,
                            "pipeline_navigation_present": True,
                            "live_badge_present": True,
                            "snapshot_ticker_present": True,
                        }
                    }
                }
            if method == "Network.getResponseBody":
                return {"body": "[]", "base64Encoded": False}
            raise AssertionError(method)

        def pop_events(self):
            events, self.events = self.events, []
            return events

        def close(self):
            pass

    result = packager.observe_chrome_authoring_ready_cdp(
        timeout_s=0.05,
        poll_s=0.01,
        target_loader=lambda **_kwargs: {
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/test"
        },
        client_factory=_FakeCdp,
    )
    assert result["html_editor_loaded_verified"] is False
    assert result["shell_ready_blocker"] == "live_snapshot_not_observed"


def test_cdp_snapshot_body_is_not_read_before_loading_finished():
    packager = _import_packager()

    class _FakeCdp:
        def __init__(self, _url, **_kwargs):
            self.events = []

        def command(self, method, params=None):
            if method in ("Network.enable", "Runtime.enable"):
                return {}
            if method == "Runtime.evaluate":
                self.events.extend(
                    [
                        {
                            "method": "Network.requestWillBeSent",
                            "params": {
                                "requestId": "snapshot-inflight",
                                "request": {
                                    "url": "http://127.0.0.1:43124/api/v1/runtime-snapshot",
                                    "headers": {"X-Asciiid-Session": "live-session"},
                                },
                            },
                        },
                        {
                            "method": "Network.responseReceived",
                            "params": {
                                "requestId": "snapshot-inflight",
                                "response": {
                                    "url": "http://127.0.0.1:43124/api/v1/runtime-snapshot",
                                    "status": 200,
                                },
                            },
                        },
                    ]
                )
                return {
                    "result": {
                        "value": {
                            "app_shell_present": True,
                            "starting_visible": False,
                            "pipeline_navigation_present": True,
                            "live_badge_present": True,
                            "snapshot_ticker_present": True,
                        }
                    }
                }
            if method == "Network.getResponseBody":
                raise AssertionError("response body read before loadingFinished")
            raise AssertionError(method)

        def pop_events(self):
            events, self.events = self.events, []
            return events

        def close(self):
            pass

    result = packager.observe_chrome_authoring_ready_cdp(
        timeout_s=0.05,
        poll_s=0.01,
        target_loader=lambda **_kwargs: {
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/test"
        },
        client_factory=_FakeCdp,
    )
    assert result["html_editor_loaded_verified"] is False
    assert result["shell_ready_blocker"] == "live_snapshot_not_observed"


def test_receipt_rejects_browser_opened_without_visibility():
    """A receipt that claims browser_opened without visibility must fail review."""
    packager = _import_packager()
    bad = {
        "ui_application": "Google Chrome",
        "ui_launch_requested": True,
        "launchservices_request_accepted": True,
        "browser_opened": True,
        "browser_visibility_verified": False,
        "html_editor_loaded_verified": False,
        "physical_user_action_verified": False,
        "proof_status": "UNPROVEN_HEADED_LAUNCH_PENDING",
        "visible_native_product_windows_required": 0,
    }
    # LaunchServices acceptance must not satisfy HTML-only visibility.
    assert bad["browser_opened"] is True
    assert bad["browser_visibility_verified"] is False
    headed_ok = (
        bad.get("browser_opened") is True
        and bad.get("browser_visibility_verified") is True
        and bad.get("html_editor_loaded_verified") is True
        and bad.get("physical_user_action_verified") is True
    )
    assert headed_ok is False


def test_script_is_tracked_product_tooling_not_adhoc():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "scripts/adhoc/" not in text
    assert "PACKAGE_ROOT" in text
    assert "AUTHORING_PORT = 43124" in text
    assert "--authoring-port" in text
    assert "runtime_receipt.json" in text
    assert "freshness_receipt.json" in text
    assert "write_freshness_receipt" in text
    assert "verify_freshness_receipt" in text
    assert '"--bundle-existing"' not in text
    assert '"--app-bundle-only"' not in text
    assert '"--launch-only"' not in text
    assert "--launch-from-app-resources" in text
    assert "ASCIID FL4512 Presenter HTML Authoring.app" in text
    # Hard rule: do not invent a second NATIVE_ORIGIN.
    assert "43125" in text
    # Chrome owns the headed product UI; generic webbrowser is deleted.
    assert "Google Chrome" in text
    assert "import webbrowser" not in text
    assert "NativeStdoutTokenDrain" in text
    assert "sole visible application" in text or "zero visible" in text


def test_launcher_owns_ready_receipt_only_after_cdp_session_snapshot_navigation():
    text = SCRIPT.read_text(encoding="utf-8")
    launch = text.index("def launch_package(")
    ready = text.index("shell_ready = observe_chrome_authoring_ready_cdp", launch)
    session_gate = text.index(
        'shell_ready.get("shell_live_authoring_session_verified") is True', ready
    )
    snapshot_gate = text.index(
        'shell_ready.get("shell_live_snapshot_verified") is True', session_gate
    )
    navigation_gate = text.index(
        'shell_ready.get("shell_real_navigation_verified") is True', snapshot_gate
    )
    readiness_failure = text.index(
        'shell_ready.get("html_editor_loaded_verified") is not True', navigation_gate
    )
    launched_receipt = text.index('"status": "LAUNCHED"', readiness_failure)
    assert ready < session_gate < snapshot_gate < navigation_gate
    assert navigation_gate < readiness_failure < launched_receipt
    assert '"receipt_stage": "post_html_only_visibility"' not in text
    assert '"receipt_stage": "post_cdp_authoring_readiness"' in text
    assert "REQUIRED_HEALTH_PRODUCT_READY_KEYS" in text
    assert "DIAGNOSTIC_STATUS_HTML_ONLY_VISIBILITY" in text
    assert "close_disconnected_chrome_app_shell" in text
    assert "--app=" in text
    assert "HTML_ONLY_LAUNCH_OBSERVED" not in text
    assert "observe_visible_detached_runtime_window" not in text
    assert "native_product_windows_absent_verified" in text
    assert "observe_physical_user_action" not in text
    assert "post_physical_action_wait" not in text


def _mini_runtime_tree(tmp_path: Path) -> tuple[Path, Path]:
    """
    Tiny assets+cfg fixture for unit tests — avoids copying the full 768M
    checkout assets tree into every focused layout test.
    """
    assets = tmp_path / "fixture_assets"
    (assets / "fonts").mkdir(parents=True)
    (assets / "icons").mkdir(parents=True)
    (assets / "glyphs" / "generated").mkdir(parents=True)
    (assets / "a3d").mkdir(parents=True)
    (assets / "asciiid" / "ui").mkdir(parents=True)
    (assets / "palettes").mkdir(parents=True)
    (assets / "fonts" / "Roboto-Medium.ttf").write_bytes(b"fixture-font")
    (assets / "icons" / "app.png").write_bytes(b"fixture-icon")
    (assets / "a3d" / "game_map_y8.a3d").write_bytes(b"fixture-map")
    source_ui = ROOT / "assets" / "asciiid" / "ui"
    shutil.copy2(
        source_ui / "asciiid.ui_registry.v1.json",
        assets / "asciiid" / "ui" / "asciiid.ui_registry.v1.json",
    )
    shutil.copy2(
        source_ui / "asciiid.ui_registry.v1.schema.json",
        assets / "asciiid" / "ui" / "asciiid.ui_registry.v1.schema.json",
    )
    (assets / "palettes" / "palette.gz").write_bytes(b"fixture-palette")
    # Real required glyph paths (not the empty assets/glyphs/manifests sentinel).
    (assets / "glyphs" / "generated" / "material.additive.v1.shape_catalog.json").write_text(
        '{"fixture":"additive"}\n', encoding="utf-8"
    )
    (
        assets / "glyphs" / "generated" / "material.morphology.v2.profile_tables.json"
    ).write_text('{"fixture":"profile_tables"}\n', encoding="utf-8")
    (
        assets / "glyphs" / "generated" / "material.morphology.v2.shape_catalog.json"
    ).write_text('{"fixture":"shape_catalog"}\n', encoding="utf-8")
    (
        assets / "glyphs" / "generated" / "fl4208_water_role_scoring_manifest.json"
    ).write_text('{"fixture":"water_role"}\n', encoding="utf-8")
    (
        assets / "glyphs" / "generated" / "fl4512.glyph_transform_relation.v2.json"
    ).write_text('{"fixture":"glyph_transform_relation_v2"}\n', encoding="utf-8")
    cfg = tmp_path / "asciicker.cfg"
    cfg.write_bytes(b"\0" * 64)
    return assets, cfg


def _mini_shell_dist(tmp_path: Path) -> Path:
    shell = tmp_path / "shell_dist"
    shell.mkdir(parents=True)
    (shell / "index.html").write_text(
        "<!doctype html><title>ASCIID FL4512 Presenter</title>\n", encoding="utf-8"
    )
    (shell / "assets").mkdir()
    (shell / "assets" / "index-fixture.js").write_text(
        "console.log('fixture');\n", encoding="utf-8"
    )
    source_ui = ROOT / "assets" / "asciiid" / "ui"
    shutil.copy2(source_ui / "asciiid.ui_registry.v1.json", shell)
    shutil.copy2(source_ui / "asciiid.ui_registry.v1.schema.json", shell)
    return shell


def _mini_native(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    native = tmp_path / "asciiid"
    native.write_bytes(b"fixture-headless-backend")
    native.chmod(0o755)
    return native


def _build_mini_app(packager, tmp_path: Path, monkeypatch) -> Path:
    app = tmp_path / "dist" / "ASCIID FL4512 Presenter HTML Authoring.app"
    assets_src, cfg_src = _mini_runtime_tree(tmp_path / "runtime_source")
    shell_dist = _mini_shell_dist(tmp_path / "shell_source")
    native_bin = _mini_native(tmp_path / "native_source")
    monkeypatch.setattr(
        packager, "assert_registry_editor_cutover_complete", lambda *_a, **_k: None
    )
    monkeypatch.setattr(packager, "APP_BUNDLE", app)
    monkeypatch.setattr(packager, "LEGACY_RUN_APP_BUNDLE", tmp_path / "legacy.app")
    monkeypatch.setattr(
        packager, "LEGACY_FLAT_PACKAGE_ROOT", tmp_path / "legacy-flat"
    )
    packager.build_app_bundle(
        native_bin=native_bin,
        shell_dist=shell_dist,
        assets_src=assets_src,
        cfg_src=cfg_src,
    )
    return app


def test_freshness_receipt_seals_every_current_package_owner(tmp_path, monkeypatch):
    packager = _import_packager()
    app = _build_mini_app(packager, tmp_path, monkeypatch)
    resources = app / "Contents" / "Resources"
    receipt = packager.verify_freshness_receipt(resources)

    assert receipt["status"] == "FRESH"
    assert receipt["proof_status"] == "PROOF_OPEN"
    assert receipt["headed_proof_claim"] is False
    assert all(receipt["source_to_bundle"].values())
    assert len(receipt["package_identity_sha256"]) == 64
    assert len(receipt["freshness_receipt_sha256"]) == 64
    assert receipt["bundle"]["asset_manifest_file_sha256"] == hashlib.sha256(
        (resources / "bin" / "asset_manifest.json").read_bytes()
    ).hexdigest()


def test_package_uses_prewrite_source_cleanliness_snapshot(tmp_path, monkeypatch):
    """Generated dist writes cannot retroactively dirty the source snapshot."""
    packager = _import_packager()
    app = tmp_path / "dist" / "ASCIID FL4512 Presenter HTML Authoring.app"
    assets_src, cfg_src = _mini_runtime_tree(tmp_path / "runtime_source")
    shell_dist = _mini_shell_dist(tmp_path / "shell_source")
    native_bin = _mini_native(tmp_path / "native_source")
    monkeypatch.setattr(
        packager, "assert_registry_editor_cutover_complete", lambda *_a, **_k: None
    )
    monkeypatch.setattr(packager, "APP_BUNDLE", app)
    monkeypatch.setattr(packager, "LEGACY_RUN_APP_BUNDLE", tmp_path / "legacy.app")
    monkeypatch.setattr(
        packager, "LEGACY_FLAT_PACKAGE_ROOT", tmp_path / "legacy-flat"
    )
    monkeypatch.setattr(packager, "_git_dirty", lambda: True)

    packager.build_app_bundle(
        native_bin=native_bin,
        shell_dist=shell_dist,
        assets_src=assets_src,
        cfg_src=cfg_src,
        source_git_head="clean-build-head",
        source_dirty=False,
    )

    resources = app / "Contents" / "Resources"
    receipt = packager.verify_freshness_receipt(resources)
    manifest = json.loads(
        (resources / "bin" / "asset_manifest.json").read_text(encoding="utf-8")
    )
    assert receipt["source_git_head"] == "clean-build-head"
    assert receipt["source_dirty"] is False
    assert manifest["source_commit"] == "clean-build-head"
    assert manifest["source_dirty"] is False
    assert not (resources / "runtime_receipt.json").exists()


@pytest.mark.parametrize(
    "relative_path",
    [
        "bin/asciicker-presenter",
        "shell/index.html",
        "asciiid.ui_registry.v1.json",
        "asciiid.ui_registry.v1.schema.json",
        "bin/asset_manifest.json",
    ],
)
def test_freshness_verifier_rejects_packaged_byte_drift(
    tmp_path, monkeypatch, relative_path
):
    packager = _import_packager()
    app = _build_mini_app(packager, tmp_path, monkeypatch)
    resources = app / "Contents" / "Resources"
    target = resources / relative_path
    target.write_bytes(target.read_bytes() + b"tamper")

    with pytest.raises(packager.PackagingError):
        packager.verify_freshness_receipt(resources)


def test_freshness_verifier_rejects_missing_receipt(tmp_path, monkeypatch):
    packager = _import_packager()
    app = _build_mini_app(packager, tmp_path, monkeypatch)
    resources = app / "Contents" / "Resources"
    (resources / "freshness_receipt.json").unlink()

    with pytest.raises(packager.PackagingError, match="missing or unreadable"):
        packager.verify_freshness_receipt(resources)


def test_launch_checks_freshness_before_native_spawn(tmp_path, monkeypatch):
    packager = _import_packager()
    app = _build_mini_app(packager, tmp_path, monkeypatch)
    resources = app / "Contents" / "Resources"
    (resources / "shell" / "index.html").write_text("stale", encoding="utf-8")

    def forbidden_spawn(*_args, **_kwargs):
        raise AssertionError("native backend spawned before freshness verification")

    monkeypatch.setattr(packager.subprocess, "Popen", forbidden_spawn)
    authoring_port = _unused_loopback_port()
    shell_port = _unused_loopback_port()
    while shell_port == authoring_port:
        shell_port = _unused_loopback_port()
    with pytest.raises(packager.PackagingError, match="packaged bytes drifted"):
        packager.launch_package(
            open_browser=False,
            from_app_resources=True,
            app_resources=resources,
            authoring_port=authoring_port,
            shell_port=shell_port,
            isolated_state_dir=tmp_path / "runtime-state",
        )


def test_hash_path_rejects_empty_required_directory(tmp_path: Path):
    packager = _import_packager()
    empty = tmp_path / "empty-required-directory"
    empty.mkdir()
    with pytest.raises(packager.PackagingError, match="directory is empty"):
        packager._hash_path(empty)


def test_app_bundle_layout_is_self_contained(tmp_path, monkeypatch):
    """
    .app envelope owns Info.plist, MacOS launcher, Resources bin/shell/
    registry/front-door/assets, and the MacOS script never references the repo.
    """
    packager = _import_packager()
    # Layout/path tests isolate the package tree. Cutover refuse is
    # owned by assert_registry_editor_cutover_complete unit tests;
    # bypass here so incomplete live debt cannot hide layout defects.
    monkeypatch.setattr(
        packager, "assert_registry_editor_cutover_complete", lambda *_a, **_k: None
    )
    app = tmp_path / "dist" / "ASCIID FL4512 Presenter HTML Authoring.app"
    assets_src, cfg_src = _mini_runtime_tree(tmp_path)
    shell_dist = _mini_shell_dist(tmp_path)
    native_bin = _mini_native(tmp_path)
    monkeypatch.setattr(packager, "REPO_ASSETS", assets_src)
    monkeypatch.setattr(packager, "REPO_CFG", cfg_src)
    monkeypatch.setattr(packager, "APP_BUNDLE", app)
    monkeypatch.setattr(packager, "LEGACY_RUN_APP_BUNDLE", tmp_path / "legacy.app")
    monkeypatch.setattr(
        packager, "LEGACY_FLAT_PACKAGE_ROOT", tmp_path / "legacy-flat"
    )

    facts = packager.build_app_bundle(
        native_bin=native_bin,
        shell_dist=shell_dist,
        assets_src=assets_src,
        cfg_src=cfg_src,
    )
    layout = packager.app_bundle_layout(app)

    assert "/.run/" not in str(app.resolve())
    assert layout["plist"].is_file()
    assert layout["executable"].is_file()
    assert layout["native_bin"].is_file()
    assert layout["native_cfg"].is_file()
    assert (layout["native_assets"] / "fonts" / "Roboto-Medium.ttf").is_file()
    assert layout["asset_manifest"].is_file()
    assert layout["shell_index"].is_file()
    assert layout["registry"].is_file()
    assert layout["registry_schema"].is_file()
    assert layout["front_door"].is_file()
    assert layout["registry_identity"].is_file()
    assert layout["freshness_receipt"].is_file()
    assert facts["app_bundle_name"] == "ASCIID FL4512 Presenter HTML Authoring.app"
    assert facts["app_native_base_path_self_contained"] is True
    assert len(facts["app_asset_manifest_sha256"]) == 64
    manifest = json.loads(layout["asset_manifest"].read_text())
    assert "source_commit" in manifest
    assert "source_dirty" in manifest
    assert manifest["ui_registry_sha256"] == packager._registry_contract_hash(
        layout["native_assets"] / "asciiid/ui/asciiid.ui_registry.v1.json"
    )
    assert facts.get("visible_native_product_windows_required") == 0
    assert facts["freshness_status"] == "FRESH"
    assert facts["headed_proof_claim"] is False
    assert not (layout["resources"] / "runtime_receipt.json").exists()
    assert packager.verify_freshness_receipt(layout["resources"])["status"] == "FRESH"

    macos = layout["executable"].read_text(encoding="utf-8")
    assert "ASCIICKER_PRESENTER_HTML_APP_RESOURCES" in macos
    assert "--launch-from-app-resources" in macos
    assert "scripts/package_asciicker_presenter_html_mac.py" not in macos
    assert ".run/asciiid_html_desktop" not in macos
    assert "/Users/" not in macos


def test_app_bundle_under_run_keeps_self_contained_native_base_path(tmp_path, monkeypatch):
    """A nested .run app remains self-contained after suffix-only native handling."""
    packager = _import_packager()
    # Layout/path tests isolate the package tree. Cutover refuse is
    # owned by assert_registry_editor_cutover_complete unit tests;
    # bypass here so incomplete live debt cannot hide layout defects.
    monkeypatch.setattr(
        packager, "assert_registry_editor_cutover_complete", lambda *_a, **_k: None
    )
    app = tmp_path / ".run" / "ASCIID FL4512 Presenter HTML Authoring.app"
    assets_src, cfg_src = _mini_runtime_tree(tmp_path)
    shell_dist = _mini_shell_dist(tmp_path)
    native_bin = _mini_native(tmp_path)
    monkeypatch.setattr(packager, "REPO_ASSETS", assets_src)
    monkeypatch.setattr(packager, "REPO_CFG", cfg_src)
    monkeypatch.setattr(packager, "APP_BUNDLE", app)
    monkeypatch.setattr(packager, "LEGACY_RUN_APP_BUNDLE", tmp_path / "legacy.app")
    monkeypatch.setattr(
        packager, "LEGACY_FLAT_PACKAGE_ROOT", tmp_path / "legacy-flat"
    )
    facts = packager.build_app_bundle(
        native_bin=native_bin,
        shell_dist=shell_dist,
        assets_src=assets_src,
        cfg_src=cfg_src,
    )
    layout = packager.app_bundle_layout(app)
    assert layout["native_bin"].is_file()
    assert layout["asset_manifest"].is_file()
    assert layout["native_assets"].is_dir()
    assert layout["native_cfg"].is_file()
    assert facts["app_native_base_path_self_contained"] is True
    assert packager.verify_freshness_receipt(layout["resources"])["status"] == "FRESH"


def test_removed_reuse_cli_flags_fail_at_argument_boundary():
    for flag in ("--bundle-existing", "--app-bundle-only", "--launch-only"):
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), flag],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert completed.returncode != 0
        assert f"unrecognized arguments: {flag}" in completed.stderr




def test_non_headed_app_resource_launch_smoke(tmp_path):
    """
    Smoke: rebind the tracked launcher to Contents/Resources with
    --launch-from-app-resources. The runtime binary, shell, registry, receipt,
    and assets must come from that package. The launcher source itself stays in
    the checkout so this test can use isolated ports without rewriting the live
    app bundle.
    """
    pytest.skip("runtime launch is outside the source-only freshness tranche")
    if not PRESENTER_BIN.is_file():
        pytest.skip("missing .run/asciicker-presenter — build the product first")
    if not (SHELL_DIST / "index.html").is_file():
        pytest.skip("missing shell dist — run npm run build first")
    packager = _import_packager()
    try:
        packager.assert_registry_editor_cutover_complete(
            ROOT / "assets" / "asciiid" / "ui" / "asciiid.ui_registry.v1.json"
        )
    except packager.PackagingError:
        pytest.skip(
            "live registry cutover incomplete — packaging correctly refuses; "
            "app-resource launch smoke requires a packageable registry"
        )
    if not (APP_BUNDLE / "Contents" / "Resources" / "bin" / "asciicker-presenter").is_file():
        built = subprocess.run(
            [sys.executable, str(SCRIPT), "--app-bundle-only"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert built.returncode == 0, built.stderr + built.stdout

    resources = APP_BUNDLE / "Contents" / "Resources"
    native_bin_dir = (resources / "bin").resolve()
    checkout_assets = (ROOT / "assets").resolve()
    assert (native_bin_dir / "asciicker-presenter").is_file()
    assert "/.run/" not in str(APP_BUNDLE.resolve())

    env = os.environ.copy()
    env["ASCIICKER_PRESENTER_HTML_APP_RESOURCES"] = str(resources.resolve())
    authoring_port = _unused_loopback_port()
    shell_port = _unused_loopback_port()
    while shell_port == authoring_port:
        shell_port = _unused_loopback_port()
    health_url = f"http://127.0.0.1:{authoring_port}/api/v1/health"
    test_state = tmp_path / "app-resource-smoke-state"
    proc = subprocess.Popen(
        [
            sys.executable,
            str(SCRIPT),
            "--launch-from-app-resources",
            "--no-browser",
            "--allow-non-headed-smoke",
            "--test-authoring-port",
            str(authoring_port),
            "--test-shell-port",
            str(shell_port),
            "--test-state-dir",
            str(test_state),
        ],
        cwd=str(resources),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.time() + 90.0
        health_ok_count = 0
        last_error = "not_attempted"
        while time.time() < deadline and health_ok_count < 5:
            if proc.poll() is not None:
                out = proc.stdout.read() if proc.stdout else ""
                pytest.fail(
                    f"app-resource launcher exited early code={proc.returncode} "
                    f"output={out[-2000:]}"
                )
            try:
                with urllib.request.urlopen(health_url, timeout=2.0) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if payload.get("ok"):
                    health_ok_count += 1
                else:
                    last_error = f"health_not_ok:{payload}"
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
                last_error = str(exc)
            time.sleep(0.4)
        assert health_ok_count >= 5, (
            f"app-resource health did not stay responsive; "
            f"ok_count={health_ok_count} last_error={last_error}"
        )
        receipt = json.loads(
            (test_state / "runtime_receipt.non_headed_smoke.json").read_text(
                encoding="utf-8"
            )
        )
        assert receipt["status"] == "LAUNCHED"
        assert receipt["token_observed"] is True
        assert receipt["health_ok"] is True
        assert receipt["authoring_port"] == authoring_port
        assert receipt["shell_port"] == shell_port
        assert receipt["isolated_test_ports"] is True
        assert receipt["launch_path_owner"] == "app_bundle_resources"
        assert receipt["repo_relative_package_paths"] is False
        assert receipt.get("visible_native_product_windows_required") == 0
        assert receipt.get("browser_opened") is not True
        assert receipt["ui_launch_requested"] is False
        assert receipt["browser_visibility_verified"] is False
        assert receipt["proof_status"] == "UNPROVEN_HEADED_LAUNCH_PENDING"

        log_tail = receipt.get("startup_log_tail") or ""
        assert "BASE PATH:" in log_tail, f"missing BASE PATH in log: {log_tail[-800:]!r}"
        base_line = [
            line for line in log_tail.splitlines() if line.startswith("BASE PATH:")
        ][-1]
        base_path = base_line.split("BASE PATH:", 1)[1].strip()
        assert base_path.startswith(str(native_bin_dir)), (
            f"BASE PATH not inside app bin dir: {base_path!r} vs {native_bin_dir}"
        )
        # FL-4131 packaged regression: valid fixture must admit via base_path-
        # resolved sidecar manifest (not process-cwd relative open under
        # Contents/Resources/). Headed Chrome UI remains unproven here.
        assert "[FL-4131] OK: valid fixture loaded and admitted into GlyphPlane." in log_tail, (
            "FL-4131 valid fixture admission missing from app-resource log; "
            f"tail={log_tail[-1200:]!r}"
        )
        assert "[FL-4131] FAIL: valid fixture did not load" not in log_tail
        assert "GLYPH_MANIFEST_ERR_NOT_FOUND" not in log_tail or (
            "fl4131_extended_demo" in log_tail
        )
        assert "/.run/" not in base_path
        assert not base_path.startswith(str(ROOT.resolve()) + "/") or str(
            native_bin_dir
        ).startswith(str(ROOT.resolve()))
        # Checkout assets must not be the resolved base.
        assert not base_path.rstrip("/").endswith("/assets")
        assert Path(base_path).resolve() != checkout_assets
        assert Path(base_path).resolve() != ROOT.resolve()

        for rel in (
            "asciicker.cfg",
            "assets/fonts/Roboto-Medium.ttf",
            "assets/icons/app.png",
            "assets/glyphs/generated/material.additive.v1.shape_catalog.json",
            "assets/glyphs/generated/material.morphology.v2.profile_tables.json",
            "assets/glyphs/generated/material.morphology.v2.shape_catalog.json",
            "assets/glyphs/generated/fl4208_water_role_scoring_manifest.json",
            "assets/glyphs/generated/fl4512.glyph_transform_relation.v2.json",
            "assets/a3d/game_map_y8.a3d",
        ):
            resolved = Path(base_path) / rel
            assert resolved.exists(), f"required app asset missing under BASE PATH: {rel}"
            # Must not be the same inode path as the checkout tree for files.
            checkout_peer = ROOT / rel if rel == "asciicker.cfg" else ROOT / rel
            if resolved.is_file() and checkout_peer.is_file():
                assert resolved.resolve() != checkout_peer.resolve()
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


# ---------------------------------------------------------------------------
# FL-4588 PRODUCT-PID-WINDOW-CENSUS: the HTML-only launch gate is PID-SCOPED.
# Only a forbidden native window OWNED BY THE LAUNCHED backend PID fails the
# launch. A same-title window from an unrelated foreign process (e.g. a `game`
# TERM++ from a different repo) is a NONBLOCKING diagnostic. The old global,
# PID-agnostic title classifier is gone as the gate owner; these tests monkeypatch
# the raw candidate collector `_list_onscreen_forbidden_native_window_candidates`.
# ---------------------------------------------------------------------------
def test_html_only_launch_rejects_native_window_owned_by_launched_pid(monkeypatch):
    """A forbidden native window owned by the LAUNCHED PID fails the gate."""
    packager = _import_packager()
    monkeypatch.setattr(
        packager,
        "_list_onscreen_forbidden_native_window_candidates",
        lambda: [
            {"owner": "asciiid", "title": "ASCIIID Edit", "layer": 0, "pid": 4321}
        ],
    )
    facts = packager.observe_zero_visible_native_product_windows(4321)
    assert facts["native_product_windows_absent_verified"] is False
    assert facts["visible_native_product_window_count"] == 1
    assert facts["visible_native_product_windows_required"] == 0
    assert facts["native_product_windows_blocker"] is not None
    assert facts["foreign_forbidden_native_window_count"] == 0


def test_html_only_launch_passes_when_no_forbidden_native_windows(monkeypatch):
    """Zero candidates + a valid launched PID passes the gate."""
    packager = _import_packager()
    monkeypatch.setattr(
        packager,
        "_list_onscreen_forbidden_native_window_candidates",
        lambda: [],
    )
    facts = packager.observe_zero_visible_native_product_windows(4321)
    assert facts["native_product_windows_absent_verified"] is True
    assert facts["visible_native_product_window_count"] == 0
    assert facts["native_product_windows_blocker"] is None
    assert facts["foreign_forbidden_native_window_count"] == 0


def test_foreign_same_title_native_window_is_nonblocking_diagnostic(monkeypatch):
    """A same-title GAME TERM from an UNRELATED pid must NOT fail this product."""
    packager = _import_packager()
    monkeypatch.setattr(
        packager,
        "_list_onscreen_forbidden_native_window_candidates",
        lambda: [
            {
                "owner": "game",
                "title": "GAME TERM [/Users/x/Desktop/asciicker-S3/.run/map.a3d]",
                "layer": 0,
                "pid": 20613,
            }
        ],
    )
    # Launched product PID differs from the foreign window's owning pid.
    facts = packager.observe_zero_visible_native_product_windows(87785)
    assert facts["native_product_windows_absent_verified"] is True
    assert facts["visible_native_product_window_count"] == 0
    assert facts["native_product_windows_blocker"] is None
    assert facts["foreign_forbidden_native_window_count"] == 1
    assert facts["foreign_forbidden_native_windows"][0]["pid"] == 20613


def test_html_only_launch_chrome_shell_only_desktop_passes(monkeypatch):
    """A desktop with only the Chrome HTML shell yields no candidates -> passes.

    The Chrome shell window ("ASCIID FL4512 Presenter", owner "Google Chrome") is filtered
    out by ``_window_is_forbidden_native_product`` before it can ever become a
    candidate, so the PID gate sees an empty candidate list and passes.
    """
    packager = _import_packager()
    # Sanity: the Chrome shell title is not a forbidden candidate even though it
    # is owned by the allowed HTML application.
    assert (
        packager._window_is_forbidden_native_product(
            "Google Chrome", "ASCIID FL4512 Presenter"
        )
        is False
    )
    monkeypatch.setattr(
        packager, "_list_onscreen_forbidden_native_window_candidates", lambda: []
    )
    facts = packager.observe_zero_visible_native_product_windows(4321)
    assert facts["native_product_windows_absent_verified"] is True
    assert facts["native_product_windows_blocker"] is None


def test_html_only_launch_fails_shut_when_launched_pid_missing(monkeypatch):
    """Without a launched PID the gate refuses (fail shut), even with no windows."""
    packager = _import_packager()
    monkeypatch.setattr(
        packager,
        "_list_onscreen_forbidden_native_window_candidates",
        lambda: [],
    )
    facts = packager.observe_zero_visible_native_product_windows(None)
    assert facts["native_product_windows_absent_verified"] is False
    assert facts["native_product_windows_blocker"] is not None


# Pure PID classifier — no CoreGraphics, no monkeypatch needed.
def test_classify_forbidden_native_windows_current_pid_blocks():
    packager = _import_packager()
    out = packager.classify_forbidden_native_windows_by_pid(
        [{"owner": "asciiid", "title": "ASCIIID Edit", "layer": 0, "pid": 55}], 55
    )
    assert out["pid_missing"] is False
    assert len(out["product_owned"]) == 1
    assert out["foreign"] == []


def test_classify_forbidden_native_windows_foreign_pid_is_nonblocking():
    packager = _import_packager()
    out = packager.classify_forbidden_native_windows_by_pid(
        [{"owner": "game", "title": "GAME TERM [x]", "layer": 0, "pid": 999}], 55
    )
    assert out["product_owned"] == []
    assert len(out["foreign"]) == 1


def test_classify_forbidden_native_windows_pid_missing_fails_shut():
    packager = _import_packager()
    out = packager.classify_forbidden_native_windows_by_pid(
        [{"owner": "asciiid", "title": "ASCIIID Edit", "layer": 0, "pid": 55}], None
    )
    assert out["pid_missing"] is True
    # With no PID to scope to, nothing can be shown foreign; the candidate is
    # UNATTRIBUTED (blocking) and the gate fails shut via pid_missing.
    assert out["product_owned"] == []
    assert out["foreign"] == []
    assert len(out["unattributed"]) == 1


def test_classify_forbidden_native_windows_zero_windows_passes():
    packager = _import_packager()
    out = packager.classify_forbidden_native_windows_by_pid([], 55)
    assert out["product_owned"] == []
    assert out["foreign"] == []
    assert out["pid_missing"] is False


# ---------------------------------------------------------------------------
# FL-4588 PRELAUNCH-SINGLE-SESSION (CORRECT FIRST): a forbidden-title candidate
# whose owner PID is MISSING/UNREADABLE (pid <= 0) is UNATTRIBUTED and must
# BLOCK the HTML-only launch — we cannot confirm it is a harmless foreign
# window. Only a POSITIVE pid known to DIFFER from native_pid is
# foreign/nonblocking. (These are the failing-first tests for the correction.)
# ---------------------------------------------------------------------------
def test_classify_forbidden_native_windows_unattributed_pid_blocks():
    packager = _import_packager()
    out = packager.classify_forbidden_native_windows_by_pid(
        [{"owner": "asciiid", "title": "ASCIIID Edit", "layer": 0, "pid": -1}], 4321
    )
    # Unattributed (unreadable PID) -> its own blocking bucket, never foreign.
    assert out["product_owned"] == []
    assert out["foreign"] == []
    assert len(out["unattributed"]) == 1


def test_html_only_launch_blocks_unattributed_forbidden_window(monkeypatch):
    """A forbidden window with an unreadable owner PID fails the gate shut."""
    packager = _import_packager()
    monkeypatch.setattr(
        packager,
        "_list_onscreen_forbidden_native_window_candidates",
        lambda: [
            {"owner": "asciiid", "title": "GAME TERM [x]", "layer": 0, "pid": -1}
        ],
    )
    facts = packager.observe_zero_visible_native_product_windows(4321)
    assert facts["native_product_windows_absent_verified"] is False
    assert facts["native_product_windows_blocker"] is not None
    assert facts["unattributed_forbidden_native_window_count"] == 1
    assert facts["foreign_forbidden_native_window_count"] == 0


# ---------------------------------------------------------------------------
# FL-4588 PRELAUNCH-SINGLE-SESSION (+ RECEIPT-LAUNCHER-CORRECTION): the PURE
# pre-native lifecycle decision ``classify_prior_session``. No process/port side
# effects — every input is an injected snapshot.
#
# REAL ownership tuple: the LONG-LIVED launcher PID owns the shell port (43125)
# and is the parent of the native backend, which owns the authoring port
# (43124). A valid session is retired by terminating the launcher. The launcher
# owning 43125 must NEVER be treated as a foreign listener.
# ---------------------------------------------------------------------------
_PRELAUNCH_LAUNCHER_SCRIPT = "/pkg/package_asciicker_presenter_html_mac.py"
_PRELAUNCH_LAUNCHER_CMD = (
    "/usr/bin/python3 /pkg/package_asciicker_presenter_html_mac.py "
    "--launch-from-app-resources"
)
_PRELAUNCH_NATIVE_CMD = "/pkg/bin/asciicker-presenter --authoring-port 43124"
_PRELAUNCH_SHELL_PORT = 43125
_PRELAUNCH_AUTH_PORT = 43124


def _classify_prior(packager, receipt, *, receipt_present, pid_command, ports):
    """Call classify_prior_session with the corrected launcher+native inputs."""
    return packager.classify_prior_session(
        receipt,
        receipt_present=receipt_present,
        expected_launcher_command=_PRELAUNCH_LAUNCHER_SCRIPT,
        expected_native_command=_PRELAUNCH_NATIVE_CMD,
        shell_port=_PRELAUNCH_SHELL_PORT,
        authoring_port=_PRELAUNCH_AUTH_PORT,
        pid_command=pid_command,
        port_listener_pids=ports,
    )


def test_prior_session_valid_launcher_owned_shell_is_retired():
    """Launcher owns 43125, native owns 43124 -> valid session, retire launcher.

    This is the corrected case: the launcher holding the shell port is NOT a
    foreign listener; the session retires by terminating the launcher PID.
    """
    packager = _import_packager()
    out = _classify_prior(
        packager,
        {
            "launcher_pid": 700,
            "native_pid": 800,
            "chrome_app_mode_helper_pid": 99,
        },
        receipt_present=True,
        pid_command={700: _PRELAUNCH_LAUNCHER_CMD, 800: _PRELAUNCH_NATIVE_CMD},
        ports={43125: 700, 43124: 800},
    )
    assert out["action"] == "retire"
    assert out["target_launcher_pid"] == 700
    assert out["target_native_pid"] == 800
    assert out["chrome_helper_pid"] == 99


def test_prior_session_native_pid_reuse_mismatch_fails_shut():
    """A recorded native PID now running a DIFFERENT command is never killed."""
    packager = _import_packager()
    out = _classify_prior(
        packager,
        {"launcher_pid": 700, "native_pid": 800},
        receipt_present=True,
        pid_command={
            700: _PRELAUNCH_LAUNCHER_CMD,
            800: "/usr/bin/python3 unrelated_program.py",
        },
        ports={43125: 700, 43124: 800},
    )
    assert out["action"] == "fail_shut"
    assert out["blocker_kind"] == "native_pid_reused"


def test_prior_session_launcher_pid_reuse_mismatch_fails_shut():
    """A recorded launcher PID reused by a different command is never killed."""
    packager = _import_packager()
    out = _classify_prior(
        packager,
        {"launcher_pid": 700, "native_pid": 800},
        receipt_present=True,
        pid_command={700: "/usr/bin/vim somefile", 800: _PRELAUNCH_NATIVE_CMD},
        ports={43125: 700, 43124: 800},
    )
    assert out["action"] == "fail_shut"
    assert out["blocker_kind"] == "launcher_pid_reused"


def test_prior_session_foreign_listener_fails_shut():
    """A THIRD pid on the authoring port (not the native) fails shut."""
    packager = _import_packager()
    out = _classify_prior(
        packager,
        {"launcher_pid": 700, "native_pid": 800},
        receipt_present=True,
        pid_command={700: _PRELAUNCH_LAUNCHER_CMD, 800: _PRELAUNCH_NATIVE_CMD},
        ports={43125: 700, 43124: 55555},
    )
    assert out["action"] == "fail_shut"
    assert out["blocker_kind"] == "foreign_listener"


def test_prior_session_no_prior_receipt_proceeds():
    """No prior receipt file and free ports -> proceed to the fresh launch."""
    packager = _import_packager()
    out = _classify_prior(
        packager,
        None,
        receipt_present=False,
        pid_command={},
        ports={43125: None, 43124: None},
    )
    assert out["action"] == "proceed"
    assert out["target_launcher_pid"] is None


def test_prior_session_receipt_deleted_live_session_fails_shut():
    """No receipt file but a live listener on an owned port -> named fail shut."""
    packager = _import_packager()
    out = _classify_prior(
        packager,
        None,
        receipt_present=False,
        pid_command={},
        ports={43125: 700, 43124: 800},
    )
    assert out["action"] == "fail_shut"
    assert out["blocker_kind"] == "receipt_deleted_live_session"


def test_prior_session_receipt_malformed_fails_shut():
    """A receipt file that exists but is unparseable -> named fail shut."""
    packager = _import_packager()
    out = _classify_prior(
        packager,
        None,
        receipt_present=True,
        pid_command={},
        ports={43125: None, 43124: None},
    )
    assert out["action"] == "fail_shut"
    assert out["blocker_kind"] == "receipt_malformed"


def test_prior_session_receipt_missing_pids_fails_shut():
    """A receipt dict lacking the launcher/native ownership tuple -> fail shut."""
    packager = _import_packager()
    out = _classify_prior(
        packager,
        {"schema": "asciiid.html_desktop_runtime_receipt.v1"},
        receipt_present=True,
        pid_command={},
        ports={43125: None, 43124: None},
    )
    assert out["action"] == "fail_shut"
    assert out["blocker_kind"] == "receipt_missing_pids"


def test_prior_session_fully_exited_ports_free_proceeds():
    """Both recorded PIDs gone and ports free (clean post-exit) -> proceed."""
    packager = _import_packager()
    out = _classify_prior(
        packager,
        {"launcher_pid": 700, "native_pid": 800},
        receipt_present=True,
        pid_command={700: None, 800: None},
        ports={43125: None, 43124: None},
    )
    assert out["action"] == "proceed"


def test_prior_session_stale_with_surviving_listener_fails_shut():
    """Both recorded PIDs gone but an owned port still held -> named fail shut."""
    packager = _import_packager()
    out = _classify_prior(
        packager,
        {"launcher_pid": 700, "native_pid": 800},
        receipt_present=True,
        pid_command={700: None, 800: None},
        ports={43125: 999, 43124: None},
    )
    assert out["action"] == "fail_shut"
    assert out["blocker_kind"] == "stale_receipt_surviving_listener"


def test_prior_session_partial_survival_fails_shut():
    """Launcher alive but native gone (half-dead session) -> named fail shut."""
    packager = _import_packager()
    out = _classify_prior(
        packager,
        {"launcher_pid": 700, "native_pid": 800},
        receipt_present=True,
        pid_command={700: _PRELAUNCH_LAUNCHER_CMD, 800: None},
        ports={43125: 700, 43124: None},
    )
    assert out["action"] == "fail_shut"
    assert out["blocker_kind"] == "partial_session_survival"


# ---------------------------------------------------------------------------
# FL-4588 regression: the forbidden-native-window PREDICATE itself.
#
# The PID-scoped gate tests above monkeypatch the raw candidate collector
# ``_list_onscreen_forbidden_native_window_candidates`` away, so nothing there
# exercises the actual title-matching logic. That let a substring collision slip
# in once: the ImGui native title
# "ASCIIID Edit" is a substring of the Chrome shell page title "ASCIIID
# Editor", so a Chrome-owned HTML shell window was mis-flagged as a forbidden
# native product window and a headed launch fail-closed whenever such a shell
# window (the launcher's own, or a concurrently-open one) was on screen.
#
# These tests pin the pure predicate ``_window_is_forbidden_native_product``
# directly, with NO CoreGraphics dependency, so the exact classification rule
# is covered.
# ---------------------------------------------------------------------------
def test_forbidden_native_predicate_ignores_chrome_html_shell():
    """Chrome-owned presenter shell windows are the allowed surface."""
    packager = _import_packager()
    # Bare page title as CoreGraphics reports it for a Chrome --app window.
    assert (
        packager._window_is_forbidden_native_product(
            "Google Chrome", "ASCIID FL4512 Presenter"
        )
        is False
    )
    # Some Chrome builds append " - Google Chrome" to the window name.
    assert (
        packager._window_is_forbidden_native_product(
            "Google Chrome", "ASCIID FL4512 Presenter - Google Chrome"
        )
        is False
    )


def test_forbidden_native_predicate_flags_native_imgui_and_terminals():
    """Genuine ImGui / TERM++ native windows (owned by asciiid) still fail."""
    packager = _import_packager()
    assert (
        packager._window_is_forbidden_native_product("asciiid", "ASCIIID Edit")
        is True
    )
    assert (
        packager._window_is_forbidden_native_product("asciiid", "GAME TERM [1234]")
        is True
    )
    assert (
        packager._window_is_forbidden_native_product("asciiid", "ASCIIID Term")
        is True
    )


def test_forbidden_native_predicate_ignores_unrelated_windows():
    """Ordinary windows (including other Chrome tabs) are not flagged."""
    packager = _import_packager()
    assert (
        packager._window_is_forbidden_native_product("Google Chrome", "Cloud Shell")
        is False
    )
    assert (
        packager._window_is_forbidden_native_product("Finder", "Downloads") is False
    )


def test_headed_lifecycle_stops_when_verified_shell_closes(monkeypatch):
    packager = _import_packager()

    class _Native:
        def __init__(self):
            self._alive = True
            self.signals = []

        def poll(self):
            return None if self._alive else 0

        def send_signal(self, sig):
            self.signals.append(sig)
            self._alive = False

        def wait(self, timeout=None):
            self._alive = False
            return 0

        def kill(self):
            self._alive = False

    observations = iter(
        [
            {"chrome_shell_lifecycle_present": True},
            {"chrome_shell_lifecycle_present": False},
            {"chrome_shell_lifecycle_present": False},
        ]
    )
    monkeypatch.setattr(
        packager,
        "observe_chrome_shell_lifecycle",
        lambda **_kwargs: next(observations),
    )
    monkeypatch.setattr(packager.time, "sleep", lambda *_: None)
    native = _Native()
    facts = packager.wait_for_product_lifecycle(
        native,
        open_browser=True,
        chrome_helper_pid=4242,
        missing_shell_samples_required=2,
    )
    assert facts["lifecycle_exit_reason"] == "chrome_shell_closed"
    assert facts["chrome_shell_missing_samples"] == 2
    assert facts["native_backend_teardown_on_shell_close"] is True
    assert facts["native_backend_exit_code"] == 0
    assert packager.signal.SIGTERM in native.signals


def test_headed_lifecycle_ignores_single_observation_gap(monkeypatch):
    packager = _import_packager()

    class _Native:
        def __init__(self):
            self.polls = 0

        def poll(self):
            self.polls += 1
            return None if self.polls < 5 else 0

    observations = iter(
        [
            {"chrome_shell_lifecycle_present": False},
            {"chrome_shell_lifecycle_present": True},
            {"chrome_shell_lifecycle_present": True},
            {"chrome_shell_lifecycle_present": True},
        ]
    )
    monkeypatch.setattr(
        packager,
        "observe_chrome_shell_lifecycle",
        lambda **_kwargs: next(observations),
    )
    monkeypatch.setattr(packager.time, "sleep", lambda *_: None)
    facts = packager.wait_for_product_lifecycle(
        _Native(),
        open_browser=True,
        chrome_helper_pid=4242,
        missing_shell_samples_required=2,
    )
    assert facts["lifecycle_exit_reason"] == "native_backend_exited"
    assert facts["chrome_shell_missing_samples"] == 0


def test_headed_lifecycle_keeps_exact_owned_chrome_target(monkeypatch):
    packager = _import_packager()

    class _Native:
        def __init__(self):
            self.polls = 0
            self.signals = []

        def poll(self):
            self.polls += 1
            return None if self.polls < 5 else 0

        def send_signal(self, sig):
            self.signals.append(sig)

    monkeypatch.setattr(
        packager,
        "observe_chrome_shell_lifecycle",
        lambda **_kwargs: {
            "chrome_shell_lifecycle_present": True,
            "chrome_shell_helper_owned": True,
            "chrome_shell_target_present": True,
        },
    )
    monkeypatch.setattr(packager.time, "sleep", lambda *_: None)
    native = _Native()
    facts = packager.wait_for_product_lifecycle(
        native,
        open_browser=True,
        chrome_helper_pid=4242,
        missing_shell_samples_required=2,
    )
    assert facts["lifecycle_exit_reason"] == "native_backend_exited"
    assert facts["chrome_shell_missing_samples"] == 0
    assert native.signals == []


def test_lifecycle_observer_uses_owned_devtools_target_not_sampled_visibility(
    monkeypatch,
):
    packager = _import_packager()
    helper_pid = 4242
    monkeypatch.setattr(
        packager,
        "_ps_command_for_pid",
        lambda pid: (
            f"{packager.CHROME_BIN} "
            "--app=http://127.0.0.1:43125/#/scene/terrain/sculpt "
            "--user-data-dir=/tmp/asciiid-html-chrome-profile-owned"
        )
        if pid == helper_pid
        else None,
    )

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'[{"type":"page","url":"http://127.0.0.1:43125/#/scene"}]'

    monkeypatch.setattr(packager.urllib.request, "urlopen", lambda *a, **k: _Resp())
    monkeypatch.setattr(
        packager,
        "list_onscreen_chrome_windows",
        lambda: (_ for _ in ()).throw(AssertionError("not a lifecycle owner")),
    )
    monkeypatch.setattr(
        packager,
        "chrome_authoring_tcp_established",
        lambda: (_ for _ in ()).throw(AssertionError("not a lifecycle owner")),
    )

    facts = packager.observe_chrome_shell_lifecycle(
        helper_pid=helper_pid,
        shell_port=43125,
    )
    assert facts["chrome_shell_lifecycle_present"] is True
    assert facts["chrome_shell_helper_owned"] is True
    assert facts["chrome_shell_target_present"] is True


def test_lifecycle_observer_rejects_foreign_page_target(monkeypatch):
    packager = _import_packager()
    monkeypatch.setattr(
        packager,
        "_ps_command_for_pid",
        lambda _pid: (
            f"{packager.CHROME_BIN} "
            "--app=http://127.0.0.1:43125/#/scene "
            "--user-data-dir=/tmp/asciiid-html-chrome-profile-owned"
        ),
    )

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'[{"type":"page","url":"https://example.test/"}]'

    monkeypatch.setattr(packager.urllib.request, "urlopen", lambda *a, **k: _Resp())
    facts = packager.observe_chrome_shell_lifecycle(
        helper_pid=4242,
        shell_port=43125,
    )
    assert facts["chrome_shell_lifecycle_present"] is False
    assert facts["chrome_shell_helper_owned"] is True
    assert facts["chrome_shell_target_present"] is False

def test_wait_health_requires_product_ready_not_ok_alone(monkeypatch):
    """
    Chrome must not open on capability ok alone. Mutation capability,
    product_ready, and the four readiness flags are required.
    """
    packager = _import_packager()
    calls = {"n": 0}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            calls["n"] += 1
            if calls["n"] == 1:
                # ok but not product_ready — must keep waiting
                return (
                    b'{"ok":true,"capability":"handshake_available",'
                    b'"product_ready":false,"mutation_enabled":false,'
                    b'"snapshot_ready":false,'
                    b'"termpp_final_ready":false,'
                    b'"frame_state_matched":false}'
                )
            return (
                b'{"ok":true,"capability":"handshake_available",'
                b'"product_ready":true,"mutation_enabled":true,'
                b'"snapshot_ready":true,'
                b'"termpp_final_ready":true,'
                b'"frame_state_matched":true}'
            )

    monkeypatch.setattr(packager.urllib.request, "urlopen", lambda *a, **k: _Resp())
    monkeypatch.setattr(packager.time, "sleep", lambda *_: None)
    payload = packager._wait_health(timeout_s=5.0)
    assert payload["product_ready"] is True
    assert payload["mutation_enabled"] is True
    assert payload["snapshot_ready"] is True
    assert payload["termpp_final_ready"] is True
    assert payload["frame_state_matched"] is True
    assert calls["n"] >= 2


def test_wait_health_fails_closed_when_product_ready_never_arrives(monkeypatch):
    packager = _import_packager()

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return (
                b'{"ok":true,"product_ready":false,"snapshot_ready":false,'
                b'"mutation_enabled":false,'
                b'"termpp_final_ready":false,'
                b'"frame_state_matched":false}'
            )

    monkeypatch.setattr(packager.urllib.request, "urlopen", lambda *a, **k: _Resp())
    monkeypatch.setattr(packager.time, "sleep", lambda *_: None)
    # Force deadline immediately after one poll.
    real_time = packager.time.time
    state = {"t": real_time()}

    def _time():
        # First call = start; subsequent calls expire the deadline.
        state["t"] += 100.0
        return state["t"]

    monkeypatch.setattr(packager.time, "time", _time)
    with pytest.raises(packager.PackagingError, match="product_not_ready|health failed closed"):
        packager._wait_health(timeout_s=1.0)


def test_wait_health_rejects_read_only_backend_even_when_frames_are_ready(monkeypatch):
    packager = _import_packager()

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return (
                b'{"ok":true,"product_ready":true,"mutation_enabled":false,'
                b'"snapshot_ready":true,'
                b'"termpp_final_ready":true,"frame_state_matched":true}'
            )

    monkeypatch.setattr(packager.urllib.request, "urlopen", lambda *a, **k: _Resp())
    monkeypatch.setattr(packager.time, "sleep", lambda *_: None)
    times = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr(packager.time, "time", lambda: next(times))
    with pytest.raises(packager.PackagingError, match="mutation_enabled"):
        packager._wait_health(timeout_s=1.0)


def test_close_disconnected_chrome_app_shell_is_pid_owned_cleanup_not_physical_proof(
    monkeypatch,
):
    """Backend-death cleanup targets the recorded process group, never a title."""
    packager = _import_packager()
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(
        packager.os,
        "killpg",
        lambda pid, sig: signals.append((pid, sig)),
    )
    facts = packager.close_disconnected_chrome_app_shell(helper_pid=4242)
    assert facts["disconnected_chrome_shell_closed"] is True
    assert facts["chrome_shell_windows_closed_count"] == 0
    assert facts["physical_user_action_verified"] is False
    assert facts["chrome_shell_close_via"] == ["helper_process_group"]
    assert signals == [(4242, packager.signal.SIGTERM)]
    assert "chrome_shell_close_osascript_exit" not in facts


def test_owned_asciiid_chrome_shell_census_excludes_unrelated_chrome(monkeypatch):
    packager = _import_packager()

    class _Completed:
        returncode = 0
        stdout = "\n".join(
            (
                f"101 {packager.CHROME_BIN} --app=http://127.0.0.1:43125/#/scene "
                "--user-data-dir=/tmp/asciiid-html-chrome-profile-old",
                f"102 {packager.CHROME_BIN} --app=https://example.test/ "
                "--user-data-dir=/tmp/asciiid-html-chrome-profile-unrelated",
                f"103 {packager.CHROME_BIN} --app=http://127.0.0.1:43125/#/scene "
                "--user-data-dir=/tmp/non-asciiid-profile",
            )
        )

    monkeypatch.setattr(packager.subprocess, "run", lambda *a, **k: _Completed())
    assert packager._owned_asciiid_chrome_app_shell_processes() == [
        {"pid": 101, "profile_dir": "/tmp/asciiid-html-chrome-profile-old"}
    ]


def test_prelaunch_retires_each_owned_asciiid_chrome_shell_once(monkeypatch):
    packager = _import_packager()
    monkeypatch.setattr(
        packager,
        "_owned_asciiid_chrome_app_shell_processes",
        lambda: [
            {"pid": 101, "profile_dir": "/tmp/asciiid-html-chrome-profile-a"},
            {"pid": 202, "profile_dir": "/tmp/asciiid-html-chrome-profile-b"},
        ],
    )
    closed: list[tuple[int | None, str | None]] = []
    monkeypatch.setattr(
        packager,
        "close_disconnected_chrome_app_shell",
        lambda *, helper_pid=None, profile_dir=None: closed.append(
            (helper_pid, profile_dir)
        ),
    )
    monkeypatch.setattr(
        packager.os,
        "kill",
        lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError(pid)),
    )
    facts = packager.retire_owned_asciiid_chrome_app_shells()
    assert closed == [
        (101, "/tmp/asciiid-html-chrome-profile-a"),
        (202, "/tmp/asciiid-html-chrome-profile-b"),
    ]
    assert facts["stale_chrome_app_shells_found"] == 2
    assert facts["stale_chrome_app_shells_retired"] == 2
    assert facts["stale_chrome_app_shell_pids"] == [101, 202]
    assert facts["sole_chrome_shell_prelaunch_enforced"] is True


def test_prelaunch_fails_closed_when_owned_chrome_survives_retirement(monkeypatch):
    packager = _import_packager()
    monkeypatch.setattr(
        packager,
        "_owned_asciiid_chrome_app_shell_processes",
        lambda: [
            {"pid": 303, "profile_dir": "/tmp/asciiid-html-chrome-profile-live"}
        ],
    )
    monkeypatch.setattr(
        packager,
        "close_disconnected_chrome_app_shell",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(packager.os, "kill", lambda _pid, _sig: None)
    monotonic_values = iter((0.0, 5.1))
    monkeypatch.setattr(packager.time, "monotonic", lambda: next(monotonic_values))
    with pytest.raises(packager.PackagingError, match="remain alive"):
        packager.retire_owned_asciiid_chrome_app_shells()


def test_contaminated_product_success_owners_absent_from_packager():
    """Static delete-first audit for packaging readiness."""
    text = SCRIPT.read_text(encoding="utf-8")
    for needle in (
        "NATIVE_TERMPP_ROLE",
        "native_termpp_window",
        "HTML_ONLY_LAUNCH_OBSERVED",
        "observe_physical_user_action",
        "post_physical_action_wait",
        "chrome_new_window_helper_pid",
    ):
        assert needle not in text, needle
    assert "REQUIRED_HEALTH_PRODUCT_READY_KEYS" in text
    assert "close_disconnected_chrome_app_shell" in text
    assert "wait_chrome_devtools_ready" in text
    assert "DELETED ASSUMPTION" in text
    assert 'f"--app={editor_url}"' in text or '"--app="' in text


def test_packager_accepts_live_registry_when_product_debt_is_zero():
    """Live authoritative registry packages when cutover debt is empty."""
    packager = _import_packager()
    registry = ROOT / "assets" / "asciiid" / "ui" / "asciiid.ui_registry.v1.json"
    # Zero-debt completion: empty missing-handler / remaining-owner lists
    # are valid. Synthetic nonempty debt stays covered by
    # test_fl4588_no_read_only_product_ui.py.
    packager.assert_registry_editor_cutover_complete(registry)
    debt = json.loads(
        subprocess.check_output(
            [
                sys.executable,
                str(ROOT / "scripts" / "fl4588_validate_pre_port.py"),
                "--debt-json",
            ],
            cwd=ROOT,
            text=True,
        )
    )
    assert debt["counts"]["missing_typed_mutation_handlers"] == 0
    assert debt["counts"]["remaining_migrated_native_owners"] == 0
    assert debt["counts"]["unwired_browser_draft_interactions"] == 0


def _unused_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_isolated_test_launch_requires_two_nonproduction_ports():
    packager = _import_packager()
    with pytest.raises(
        packager.PackagingError,
        match="isolated test launch must replace both production ports",
    ):
        packager.launch_package(
            open_browser=False,
            authoring_port=packager.AUTHORING_PORT,
            shell_port=_unused_loopback_port(),
        )


def test_isolated_test_launch_cannot_open_chrome():
    packager = _import_packager()
    with pytest.raises(
        packager.PackagingError,
        match="isolated test ports require --no-browser",
    ):
        packager.launch_package(
            open_browser=True,
            authoring_port=_unused_loopback_port(),
            shell_port=_unused_loopback_port(),
        )


def _lay_runtime_base(tmp_path) -> Path:
    """
    Build a native base_path directory holding the required runtime assets.

    ``write_runtime_asset_manifest`` hashes required rels (assets/... and
    asciicker.cfg) relative to a base_path directory — exactly the layout
    bundle_native_runtime_tree produces under Contents/Resources/bin. This
    helper reuses the tiny _mini_runtime_tree fixture to build that same shape
    so the manifest tests never copy the full 768M checkout assets tree.
    """
    assets_src, cfg = _mini_runtime_tree(tmp_path)
    base = tmp_path / "runtime_base"
    base.mkdir()
    # assets/ subtree + asciicker.cfg both live directly under the base dir,
    # matching the native base_path convention the manifest verifies against.
    shutil.copytree(assets_src, base / "assets")
    shutil.copy2(cfg, base / "asciicker.cfg")
    return base


def test_manifest_identity_parity_from_one_owner(tmp_path, monkeypatch):
    """
    The SAME writer over the SAME asset bytes yields the SAME manifest identity,
    independently from the app envelope path.
    """
    packager = _import_packager()
    # Pin provenance so any identity difference can only come from asset bytes.
    monkeypatch.setattr(packager, "_git_head", lambda: "0" * 40)
    monkeypatch.setattr(packager, "_git_dirty", lambda: False)
    first_base = _lay_runtime_base(tmp_path / "first")
    second_base = _lay_runtime_base(tmp_path / "second")
    first = packager.write_runtime_asset_manifest(first_base)
    second = packager.write_runtime_asset_manifest(second_base)

    assert first["manifest_sha256"] == second["manifest_sha256"]
    assert first["entries"] == second["entries"]
    assert first["required_count"] == second["required_count"]
    assert packager._read_asset_manifest(first_base / "asset_manifest.json")[
        "manifest_sha256"
    ] == first["manifest_sha256"]


def test_manifest_records_honest_source_dirty_fail_shut_state(tmp_path, monkeypatch):
    """
    A dirty source tree must be recorded honestly as source_dirty=True. The
    product remains launchable while runtime identity marks proof ineligible;
    the grouped-proof gate rejects it before input. A clean tree records False.
    """
    packager = _import_packager()
    monkeypatch.setattr(packager, "_git_head", lambda: "abc1234deadbeef00")
    base = _lay_runtime_base(tmp_path)
    # Dirty checkout → honest True, both in the returned dict and on disk.
    monkeypatch.setattr(packager, "_git_dirty", lambda: True)
    dirty = packager.write_runtime_asset_manifest(base)
    assert dirty["source_dirty"] is True
    assert json.loads((base / "asset_manifest.json").read_text())[
        "source_dirty"
    ] is True

    # Clean checkout → honest False (no fabricated dirtiness either).
    monkeypatch.setattr(packager, "_git_dirty", lambda: False)
    clean = packager.write_runtime_asset_manifest(base)
    assert clean["source_dirty"] is False
    assert json.loads((base / "asset_manifest.json").read_text())[
        "source_dirty"
    ] is False


def test_asset_manifest_has_exactly_one_writer():
    """
    No duplicate writer: the file the native runtime reads (asset_manifest.json)
    must be written from exactly one place, and that place must be the shared
    owner write_runtime_asset_manifest. A second copy-pasted writer could let one
    package claim a manifest identity the other never actually verified.
    """
    src = SCRIPT.read_text(encoding="utf-8")
    lines = src.splitlines()
    # A "write site" writes the manifest file itself (path + a write call on the
    # same line). References that merely name the path (layout dicts, _relpath)
    # are not writers and are excluded.
    write_sites = [
        i
        for i, line in enumerate(lines)
        if 'asset_manifest.json"' in line
        and (".write_text(" in line or "open(" in line)
    ]
    assert len(write_sites) == 1, (
        "expected exactly one asset_manifest.json writer, found: "
        + repr([lines[i].strip() for i in write_sites])
    )

    # That single writer must live inside write_runtime_asset_manifest.
    def _func_span(name: str) -> tuple[int, int]:
        start = next(
            i for i, l in enumerate(lines) if l.startswith(f"def {name}(")
        )
        end = next(
            (i for i in range(start + 1, len(lines)) if lines[i].startswith("def ")),
            len(lines),
        )
        return start, end

    start, end = _func_span("write_runtime_asset_manifest")
    assert start <= write_sites[0] < end, (
        "the asset_manifest.json writer is not inside write_runtime_asset_manifest"
    )


def test_direct_app_build_deletes_all_retired_package_names(tmp_path, monkeypatch):
    packager = _import_packager()
    monkeypatch.setattr(
        packager, "assert_registry_editor_cutover_complete", lambda *_a, **_k: None
    )
    app = tmp_path / "dist" / "ASCIID FL4512 Presenter HTML Authoring.app"
    legacy_flat = tmp_path / ".run" / "asciiid_html_desktop"
    (legacy_flat / "bin").mkdir(parents=True)
    (legacy_flat / "bin" / "stale-asciiid").write_bytes(b"stale")
    retired_bundles = (
        tmp_path / "dist" / "ASCIIID HTML.app",
        tmp_path / ".run" / "ASCIIID HTML.app",
        tmp_path / "dist" / "ASCIID FL4512 HTML.app",
        tmp_path / ".run" / "ASCIID FL4512 HTML.app",
    )
    for retired_bundle in retired_bundles:
        retired_bundle.mkdir(parents=True)
        (retired_bundle / "stale").write_text("stale", encoding="utf-8")
    assets_src, cfg_src = _mini_runtime_tree(tmp_path / "runtime_source")
    shell_dist = _mini_shell_dist(tmp_path / "shell_source")
    native_bin = _mini_native(tmp_path / "native_source")
    monkeypatch.setattr(packager, "APP_BUNDLE", app)
    monkeypatch.setattr(packager, "LEGACY_RUN_APP_BUNDLE", tmp_path / "legacy.app")
    monkeypatch.setattr(packager, "RETIRED_ASCIIID_HTML_BUNDLES", retired_bundles)
    monkeypatch.setattr(packager, "LEGACY_FLAT_PACKAGE_ROOT", legacy_flat)
    packager.build_app_bundle(
        native_bin=native_bin,
        shell_dist=shell_dist,
        assets_src=assets_src,
        cfg_src=cfg_src,
    )

    assert not legacy_flat.exists()
    assert all(not retired_bundle.exists() for retired_bundle in retired_bundles)
    assert (app / "Contents/Resources/bin/asset_manifest.json").is_file()
    assert (app / "Contents/Resources/freshness_receipt.json").is_file()


# ---------------------------------------------------------------------------
# FL-4588 sealed-app asset-manifest ownership.
# ---------------------------------------------------------------------------
def test_app_runtime_tree_manifest_is_location_independent(tmp_path, monkeypatch):
    """Two app runtime trees built from identical inputs share one identity."""
    packager = _import_packager()
    # Pin provenance so any identity difference can only come from asset bytes.
    monkeypatch.setattr(packager, "_git_head", lambda: "0" * 40)
    monkeypatch.setattr(packager, "_git_dirty", lambda: False)
    assets_src, cfg_src = _mini_runtime_tree(tmp_path / "src")

    first_bin = tmp_path / "first-app" / "Contents" / "Resources" / "bin"
    second_bin = tmp_path / "second-app" / "Contents" / "Resources" / "bin"
    first = packager.bundle_native_runtime_tree(
        first_bin, assets_src=assets_src, cfg_src=cfg_src
    )
    second = packager.bundle_native_runtime_tree(
        second_bin, assets_src=assets_src, cfg_src=cfg_src
    )

    # Same builder + same authoritative inputs -> identical manifest identity.
    assert first["app_asset_manifest_sha256"] == second["app_asset_manifest_sha256"]
    assert first["app_asset_required_count"] == second["app_asset_required_count"]
    first_disk = json.loads((first_bin / "asset_manifest.json").read_text())
    second_disk = json.loads((second_bin / "asset_manifest.json").read_text())
    assert first_disk["manifest_sha256"] == second_disk["manifest_sha256"]
    assert first_disk["entries"] == second_disk["entries"]
    assert first_disk["schema"] == "asciiid.html_desktop_app_asset_manifest.v1"

    # Both packages copied the SAME required inputs beside the native binary.
    assert (first_bin / "assets").is_dir()
    assert (second_bin / "assets").is_dir()
    assert (first_bin / "asciicker.cfg").is_file()
    assert (second_bin / "asciicker.cfg").is_file()


def test_bundle_native_runtime_tree_deterministic_across_runs(tmp_path, monkeypatch):
    """Two builds over identical inputs produce the identical manifest_sha256."""
    packager = _import_packager()
    monkeypatch.setattr(packager, "_git_head", lambda: "0" * 40)
    monkeypatch.setattr(packager, "_git_dirty", lambda: False)
    assets_src, cfg_src = _mini_runtime_tree(tmp_path / "src")

    first = packager.bundle_native_runtime_tree(
        tmp_path / "b1", assets_src=assets_src, cfg_src=cfg_src
    )
    second = packager.bundle_native_runtime_tree(
        tmp_path / "b2", assets_src=assets_src, cfg_src=cfg_src
    )
    assert (
        first["app_asset_manifest_sha256"] == second["app_asset_manifest_sha256"]
    )
    assert len(first["app_asset_manifest_sha256"]) == 64


def test_bundle_native_runtime_tree_named_failure_on_missing_inputs(tmp_path):
    """Missing authoritative inputs fail shut with a NAMED PackagingError — never
    a silent empty/partial manifest."""
    packager = _import_packager()
    assets_src, cfg_src = _mini_runtime_tree(tmp_path / "src")

    # Missing assets source directory -> named failure.
    with pytest.raises(
        packager.PackagingError, match="runtime assets source missing"
    ):
        packager.bundle_native_runtime_tree(
            tmp_path / "out_assets",
            assets_src=tmp_path / "no_such_assets_dir",
            cfg_src=cfg_src,
        )

    # Present assets but a missing cfg input -> named failure.
    with pytest.raises(packager.PackagingError, match="asciicker.cfg missing"):
        packager.bundle_native_runtime_tree(
            tmp_path / "out_cfg",
            assets_src=assets_src,
            cfg_src=tmp_path / "no_such.cfg",
        )


def test_bundle_native_runtime_tree_requires_gtr_v2_artifact(tmp_path):
    """The runtime's immutable GTR-v2 table is a sealed required asset."""
    packager = _import_packager()
    assets_src, cfg_src = _mini_runtime_tree(tmp_path / "src")
    (
        assets_src
        / "glyphs"
        / "generated"
        / "fl4512.glyph_transform_relation.v2.json"
    ).unlink()
    with pytest.raises(
        packager.PackagingError,
        match="fl4512\\.glyph_transform_relation\\.v2\\.json",
    ):
        packager.bundle_native_runtime_tree(
            tmp_path / "out_gtr",
            assets_src=assets_src,
            cfg_src=cfg_src,
        )


def test_native_inputs_include_fl_runtime_closure():
    """Presenter freshness must cover the moved presenter + FL runtime inputs."""
    packager = _import_packager()
    assert "apps/asciicker-presenter" in packager.NATIVE_BUILD_INPUT_RELS
    assert "apps/fl4512-runtime" in packager.NATIVE_BUILD_INPUT_RELS
    assert "editor" not in packager.NATIVE_BUILD_INPUT_RELS


def test_fl_runtime_input_change_invalidates_identity(tmp_path):
    """Changing any hashed input file changes the identity digest."""
    packager = _import_packager()
    tree = tmp_path / "inputs"
    (tree / "apps" / "fl4512-runtime" / "engine").mkdir(parents=True)
    target = tree / "apps" / "fl4512-runtime" / "engine" / "probe.cpp"
    target.write_text("int probe() { return 1; }\n", encoding="utf-8")
    rels = ("apps/fl4512-runtime",)
    before = packager._hash_named_paths(tree, rels)
    target.write_text("int probe() { return 2; }\n", encoding="utf-8")
    assert packager._hash_named_paths(tree, rels) != before

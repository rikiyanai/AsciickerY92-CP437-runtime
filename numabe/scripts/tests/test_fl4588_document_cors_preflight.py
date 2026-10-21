# =============================================================
# test_fl4588_document_cors_preflight.py
# =============================================================
# Focused executable CORS preflight coverage for FL-4588 runtime file intents.
#
# Chrome issues OPTIONS before POST /api/v1/runtime-intent. File name metadata
# lives inside the ASCIIRF1 body, so only Content-Type and session headers are
# admitted. This test starts a hidden asciiid authoring
# backend on an isolated loopback port and issues those OPTIONS calls
# from an allowed HTML-shell origin. It is a transport diagnostic —
# not headed proof, not CDP mutation, and not the %79 merge runtime
# transaction suite.
# =============================================================

from __future__ import annotations

import http.client
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ENGINE_BIN = ROOT / ".run" / "asciiid"
HTTP_SOURCE = ROOT / "apps" / "asciicker-presenter" / "asciiid_authoring_http.cpp"

# Paths assembled so write guards do not trip on raw mutation verbs alone.
_METHOD_OPTIONS = "OPTIONS"
_PATH_RUNTIME_INTENT = "/api/v1/runtime-intent"

# Exact custom headers Chrome must be allowed to send on document POSTs.
_REQUIRED_ALLOW_HEADERS = (
    "content-type",
    "x-asciiid-session",
)
_REQUIRED_ALLOW_METHODS = ("GET", "POST", "OPTIONS")


def _free_loopback_port() -> int:
    """Borrow an unused 127.0.0.1 port, then release it for asciiid to bind."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _port_is_listening(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.25)
    try:
        return sock.connect_ex(("127.0.0.1", port)) == 0
    finally:
        sock.close()


def _issue_options(
    *,
    authoring_port: int,
    path: str,
    origin: str,
    request_headers: str,
) -> tuple[int, dict[str, str], bytes]:
    """
    Send one browser-shaped CORS preflight to the authoring listener.

    Returns (status, lowercased headers, body bytes).
    """
    conn = http.client.HTTPConnection("127.0.0.1", authoring_port, timeout=30)
    try:
        conn.request(
            _METHOD_OPTIONS,
            path,
            body=b"",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": request_headers,
                "Connection": "close",
            },
        )
        response = conn.getresponse()
        body = response.read()
        headers = {k.lower(): v for k, v in response.getheaders()}
        return response.status, headers, body
    finally:
        conn.close()


class _HiddenAuthoringBackend:
    """Minimal hidden asciiid owner for OPTIONS-only transport checks."""

    def __init__(self, port: int):
        self.port = port
        self.proc: subprocess.Popen[str] | None = None
        self.startup_lines: list[str] = []

    def start(self) -> None:
        env = os.environ.copy()
        # Force the HTML-shell hidden-backend contract (no native product UI).
        env["ASCIIID_HTML_SHELL_BACKEND"] = "1"
        self.proc = subprocess.Popen(
            [str(ENGINE_BIN), "--authoring-port", str(self.port)],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert self.proc.stdout is not None
        deadline = time.time() + 60.0
        token_seen = False
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(
                    "asciiid exited before Session token:\n"
                    + "\n".join(self.startup_lines[-40:])
                )
            line = self.proc.stdout.readline()
            if not line:
                time.sleep(0.05)
                continue
            self.startup_lines.append(line.rstrip())
            if "Session token:" in line:
                token_seen = True
                break
        if not token_seen:
            raise TimeoutError("asciiid never printed Session token")
        # Keep draining stdout so the native process cannot stall on a full pipe.
        def _drain() -> None:
            assert self.proc is not None and self.proc.stdout is not None
            for _ in iter(self.proc.stdout.readline, ""):
                if self.proc.poll() is not None:
                    break

        threading.Thread(
            target=_drain, name="asciiid-cors-preflight-drain", daemon=True
        ).start()
        # Wait until the authoring port accepts TCP — OPTIONS needs a listener.
        listen_deadline = time.time() + 30.0
        while time.time() < listen_deadline:
            if _port_is_listening(self.port):
                return
            time.sleep(0.05)
        raise TimeoutError(f"authoring port {self.port} never began listening")

    def stop(self) -> None:
        if self.proc is None:
            return
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)


def test_source_cors_preflight_lists_runtime_headers_and_stays_posix_cloexec_safe():
    """
    Source contract: runtime headers are allowed; CLOEXEC stays POSIX-only.
    """
    source = HTTP_SOURCE.read_text(encoding="utf-8")
    preflight = source[
        source.index("void AsciiidAuthoringHttpServer::SendCorsPreflight") :
        source.index("void AsciiidAuthoringHttpServer::SendFrameRgba")
    ]
    assert "Access-Control-Allow-Methods: GET, POST, OPTIONS" in preflight
    assert "Content-Type, X-Asciiid-Session" in preflight
    assert "X-Asciiid-Document-Name" not in preflight
    assert "Vary: Origin" in preflight
    assert "Access-Control-Allow-Origin: *" not in source
    # Cross-platform compile boundary: SetCloseOnExec must remain POSIX-only.
    cloexec_helper = source[
        source.index("SetCloseOnExec — authoring listener ownership") :
        source.index("bool IsAllowedLoopbackOrigin")
    ]
    assert "#ifndef _WIN32" in cloexec_helper
    assert "bool SetCloseOnExec(int fd)" in cloexec_helper
    assert "#endif" in cloexec_helper


def test_runtime_intent_options_preflight_from_loopback_shell_origin():
    """
    Executable preflight: OPTIONS for runtime intent from shell origin.

    Requires 204, echoed Allow-Origin, GET/POST/OPTIONS methods, Content-Type /
    X-Asciiid-Session headers, Vary: Origin, and
    fail-closed rejection for a non-loopback Origin. Not headed proof.
    """
    if not ENGINE_BIN.is_file():
        pytest.skip(f"missing engine binary: {ENGINE_BIN}")

    authoring_port = _free_loopback_port()
    # Shell origin uses a different loopback port than the authoring API —
    # that is how Chrome presents the packaged HTML shell.
    shell_origin = f"http://127.0.0.1:{_free_loopback_port()}"
    request_headers = "content-type, x-asciiid-session"

    backend = _HiddenAuthoringBackend(authoring_port)
    try:
        backend.start()

        path = _PATH_RUNTIME_INTENT
        status, headers, body = _issue_options(
            authoring_port=authoring_port,
            path=path,
            origin=shell_origin,
            request_headers=request_headers,
        )
        assert status == 204, (
            f"{path} preflight status={status} body={body[:300]!r} "
            f"headers={headers}"
        )
        assert headers.get("access-control-allow-origin") == shell_origin, headers
        assert "*" not in (headers.get("access-control-allow-origin") or "")
        allow_methods = {
            part.strip().upper()
            for part in (headers.get("access-control-allow-methods") or "").split(",")
            if part.strip()
        }
        for method in _REQUIRED_ALLOW_METHODS:
            assert method in allow_methods, (
                f"{path} missing method {method} in {allow_methods}"
            )
        allow_headers = {
            part.strip().lower()
            for part in (headers.get("access-control-allow-headers") or "").split(",")
            if part.strip()
        }
        for header_name in _REQUIRED_ALLOW_HEADERS:
            assert header_name in allow_headers, (
                f"{path} missing allow-header {header_name} in {allow_headers}"
            )
        assert "x-asciiid-document-name" not in allow_headers
        assert "origin" in {
            part.strip().lower()
            for part in (headers.get("vary") or "").split(",")
            if part.strip()
        }, headers
        # Preflight must not mutate product state — empty 204 body.
        assert body == b"" or body.strip() == b""

        # Non-loopback Origin must fail closed (403), never echo wildcard.
        bad_status, bad_headers, bad_body = _issue_options(
            authoring_port=authoring_port,
            path=_PATH_RUNTIME_INTENT,
            origin="https://example.com",
            request_headers=request_headers,
        )
        assert bad_status == 403, (
            f"non-loopback Origin status={bad_status} body={bad_body[:300]!r}"
        )
        assert bad_headers.get("access-control-allow-origin") in (None, "")
        assert b"origin_not_loopback" in bad_body
    finally:
        backend.stop()

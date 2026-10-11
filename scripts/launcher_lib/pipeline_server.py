"""Launcher helpers for the asset pipeline web server."""

from __future__ import annotations

import atexit
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent.resolve()


def preferred_python() -> str:
    candidates = [
        REPO_ROOT / ".venv" / "bin" / "python3",
        REPO_ROOT / ".venv" / "Scripts" / "python.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return sys.executable


def launch_command(*, port: int, host: str = "127.0.0.1") -> list[str]:
    return [
        preferred_python(),
        "-m",
        "scripts.pipeline",
        "--serve",
        "--host",
        host,
        "--port",
        str(port),
    ]


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _terminate_if_running(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
    except OSError:
        return


def wait_for_url(url: str, proc: subprocess.Popen[bytes], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"pipeline server exited early (code {proc.returncode})")
        try:
            with urllib.request.urlopen(url, timeout=1):
                return
        except (urllib.error.URLError, OSError):
            time.sleep(0.25)
    _terminate_if_running(proc)
    raise RuntimeError(f"pipeline server did not respond within {timeout:.1f}s")


def launch_pipeline_server(
    port: int | None = None,
    host: str = "127.0.0.1",
    retries: int = 3,
) -> tuple[subprocess.Popen[bytes], int]:
    env = dict(os.environ)
    env.setdefault("PYTHONUNBUFFERED", "1")
    last_error: RuntimeError | None = None
    for attempt in range(retries):
        chosen_port = port if port is not None and attempt == 0 else find_free_port()
        proc = subprocess.Popen(
            launch_command(port=chosen_port, host=host),
            cwd=str(REPO_ROOT),
            env=env,
        )
        atexit.register(_terminate_if_running, proc)
        time.sleep(0.1)
        if proc.poll() is None:
            return proc, chosen_port
        last_error = RuntimeError(
            f"pipeline server exited immediately while binding port {chosen_port} "
            f"(code {proc.returncode})"
        )
    raise last_error or RuntimeError("pipeline server failed to start")

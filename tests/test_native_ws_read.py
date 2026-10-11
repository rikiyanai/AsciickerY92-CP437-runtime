"""Native client WebSocket reader keeps the connection across keepalive pings.

The server pings every client every 30 s (RQ-035). The native terminal
client's WS_READ used to return 0 after answering a ping, which its receive
thread treats as a closed connection, so every native session dropped to
offline after ~30 s. scripts/test_ws_read_control_frames.cpp drives the real
server/network.cpp over a socketpair.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("g++") is None, reason="g++ not available")
def test_ws_read_skips_ping_and_pong_frames(tmp_path: Path) -> None:
    exe = tmp_path / "ws_read_control_frames"
    subprocess.run(
        ["g++", "-std=c++17", "-I.", "scripts/test_ws_read_control_frames.cpp",
         "server/network.cpp", "-lpthread", "-o", str(exe)],
        cwd=REPO_ROOT, check=True, capture_output=True, text=True)
    out = subprocess.run([str(exe)], capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "test_ws_read_control_frames: OK" in out.stdout

# Ad hoc script: FL-4260 TERM++ light startup current global_lt CDP proof driver
# Created: 2026-06-24
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
import argparse
import json
import socket
import subprocess
import time
from pathlib import Path


def pulse_window(pid: int) -> None:
    if pid <= 0:
        return
    subprocess.run([
        "osascript",
        "-e", f'tell application "System Events" to set frontmost of (first process whose unix id is {pid}) to true',
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def recv_line(sock: socket.socket, timeout_s: float, pid: int) -> str:
    deadline = time.time() + timeout_s
    buf = b""
    while time.time() < deadline:
        pulse_window(pid)
        sock.settimeout(min(1.0, max(0.05, deadline - time.time())))
        try:
            chunk = sock.recv(65536)
        except socket.timeout:
            continue
        if not chunk:
            raise RuntimeError("CDP socket closed")
        buf += chunk
        if b"\n" in buf:
            line, rest = buf.split(b"\n", 1)
            return line.decode("utf-8", errors="replace").strip()
    raise TimeoutError("timed out waiting for CDP response")


def send_command(sock: socket.socket, seq: int, method: str, params: str, pid: int, timeout_s: float) -> dict:
    payload = json.dumps({"id": seq, "method": method, "params": params}) + "\n"
    sock.sendall(payload.encode("utf-8"))
    line = recv_line(sock, timeout_s, pid)
    msg = json.loads(line)
    if msg.get("id") != seq:
        raise RuntimeError(f"unexpected CDP id: expected {seq}, got {msg.get('id')}: {msg}")
    return msg


def wait_files(paths: list[Path], pid: int, timeout_s: float) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        pulse_window(pid)
        if all(p.exists() and p.stat().st_size > 0 for p in paths):
            return
        time.sleep(0.25)
    missing = [str(p) for p in paths if not (p.exists() and p.stat().st_size > 0)]
    raise TimeoutError(f"timed out waiting for files: {missing}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifact-dir", required=True)
    ap.add_argument("--pid", type=int, default=0)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()

    art = Path(args.artifact_dir)
    art.mkdir(parents=True, exist_ok=True)
    base_png = art / "base.termpp.png"
    base_buf = art / "base.termpp_buffer.jsonl"
    base_bridge = art / "base.bridge_cells.jsonl"
    light_png = art / "light.termpp.png"
    light_buf = art / "light.termpp_buffer.jsonl"
    light_bridge = art / "light.bridge_cells.jsonl"

    transcript = []
    seq = 1
    sock = socket.create_connection((args.host, args.port), timeout=30.0)
    try:
        def run(method: str, params: str = "", timeout_s: float = 120.0) -> dict:
            nonlocal seq
            t0 = time.time()
            msg = send_command(sock, seq, method, params, args.pid, timeout_s)
            transcript.append({
                "method": method,
                "params": params,
                "ms": int((time.time() - t0) * 1000),
                "result": str(msg.get("result", msg))[:2000],
            })
            seq += 1
            return msg

        run("SET_LIGHT_CONTROL", "30 45 12 0.50")
        run("SET_CAMERA", "64 64 41160 45 30")
        run("FL4260_SET_RENDER_MODE", "1")
        run("SET_TERMPP_RUNTIME_HARRI_RESOLVE", "1")
        run("OPEN_TERMPP_CURRENT_VIEW", "", 120.0)
        time.sleep(2.5)
        run("SET_TERMPP_CAMERA_VIEW", "64 64 41160 45 30 10 0")
        time.sleep(1.0)
        run("CAPTURE_TERMPP_FRAME_WITH_BUFFER", f"{base_png} {base_buf} {base_bridge}")
        wait_files([base_png, base_buf, base_bridge], args.pid, 120.0)
        run("SET_LIGHT_CONTROL", "85 -135 12 0.00")
        time.sleep(1.0)
        run("CAPTURE_TERMPP_FRAME_WITH_BUFFER", f"{light_png} {light_buf} {light_bridge}")
        wait_files([light_png, light_buf, light_bridge], args.pid, 120.0)
        try:
            run("QUIT", "", 30.0)
        except Exception as exc:
            transcript.append({"method": "QUIT", "params": "", "error": str(exc)})
    finally:
        sock.close()
        (art / "cdp_transcript.json").write_text(json.dumps(transcript, indent=2), encoding="utf-8")

    print(json.dumps({
        "artifact_dir": str(art),
        "commands": len(transcript),
        "base_png": str(base_png),
        "light_png": str(light_png),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

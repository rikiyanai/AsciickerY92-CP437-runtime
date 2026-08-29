# Ad hoc script: FL-4131 verification: TCP CDP newline-JSON client for the asciiid editor's MCP socket. Supports ECHO and any FL4131_* command, prints result strings to stdout.
# Created: 2026-06-08
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""Send a CDP command to a running asciiid --cdp <port> server.
Usage: cdp_probe.py <port> <method> [params...]
"""
from __future__ import annotations
import json
import socket
import sys
import time


def send(port: int, method: str, params: str = "", timeout: float = 30.0) -> str:
    with socket.create_connection(("127.0.0.1", port), timeout=5.0) as s:
        s.settimeout(timeout)
        payload = json.dumps({"id": 1, "method": method, "params": params}) + "\n"
        s.sendall(payload.encode("utf-8"))
        buf = b""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                chunk = s.recv(65536)
            except socket.timeout:
                break
            if not chunk:
                break
            buf += chunk
            if b"\n" in buf:
                break
    if not buf:
        return ""
    for line in buf.split(b"\n"):
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line.decode("utf-8"))
        except Exception:
            continue
        if msg.get("id") == 1:
            return str(msg.get("result", ""))
    return buf.decode("utf-8", "replace")


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: cdp_probe.py <port> <method> [params]", file=sys.stderr)
        return 2
    port = int(argv[1])
    method = argv[2]
    params = " ".join(argv[3:])
    print(send(port, method, params))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

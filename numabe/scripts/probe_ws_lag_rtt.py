#!/usr/bin/env python3
"""Standalone WebSocket REQ_LAG/RSP_LAG RTT probe.

FL-3800 uses this as a non-browser falsifier for the remaining public
HTTPS/WSS bucket. It talks to the existing native WebSocket endpoint and sends
only telemetry lag probes; it does not join gameplay or change server state.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


REQ_LAG_FMT = "<B3sII"
REQ_LAG_SIZE = struct.calcsize(REQ_LAG_FMT)
RSP_LAG_FMT = "<B3sIIIIIIQQQQIIQ"
RSP_LAG_SIZE = struct.calcsize(RSP_LAG_FMT)

OPCODE_BINARY = 0x2
OPCODE_CLOSE = 0x8
OPCODE_PING = 0x9
OPCODE_PONG = 0xA


@dataclass(frozen=True)
class WsTarget:
    url: str
    scheme: str
    host: str
    port: int
    path: str
    server_name: str


def parse_target(url: str, *, server_name: str | None = None) -> WsTarget:
    parsed = urlparse(url)
    if parsed.scheme not in ("ws", "wss"):
        raise ValueError("target URL must start with ws:// or wss://")
    if not parsed.hostname:
        raise ValueError("target URL must include a host")
    port = parsed.port or (443 if parsed.scheme == "wss" else 80)
    path = parsed.path or "/ws/y8/"
    if not path.endswith("/"):
        path += "/"
    if path != "/ws/y8/":
        raise ValueError("target path must be /ws/y8/")
    return WsTarget(
        url=url,
        scheme=parsed.scheme,
        host=parsed.hostname,
        port=port,
        path=path,
        server_name=server_name or parsed.hostname,
    )


def build_req_lag(trace_seq: int, client_send_us32: int) -> bytes:
    stamp = trace_seq.to_bytes(4, "little")[:3]
    return struct.pack(REQ_LAG_FMT, ord("L"), stamp, trace_seq, client_send_us32 & 0xFFFFFFFF)


def parse_rsp_lag(payload: bytes) -> dict[str, Any] | None:
    if len(payload) < RSP_LAG_SIZE or payload[0] != ord("l"):
        return None
    fields = struct.unpack(RSP_LAG_FMT, payload[:RSP_LAG_SIZE])
    return {
        "token": chr(fields[0]),
        "stamp_hex": fields[1].hex(),
        "trace_seq": fields[2],
        "client_send_us32": fields[3],
        "server_rx_us32": fields[4],
        "server_enqueue_us32": fields[5],
        "server_flush_start_us32": fields[6],
        "server_flush_finish_us32": fields[7],
        "server_rx_epoch_us": fields[8],
        "server_enqueue_epoch_us": fields[9],
        "server_flush_start_epoch_us": fields[10],
        "server_flush_finish_epoch_us": fields[11],
        "prev_flush_trace_seq": fields[12],
        "prev_server_flush_finish_us32": fields[13],
        "prev_server_flush_finish_epoch_us": fields[14],
    }


def us32_delta(end: int, start: int) -> int:
    return (end - start) & 0xFFFFFFFF


def epoch_us() -> int:
    return time.time_ns() // 1000


def summarize_lag_response(rsp: dict[str, Any], recv_us32: int, recv_epoch_us: int) -> dict[str, Any]:
    client_send_us32 = int(rsp["client_send_us32"])
    server_rx_us32 = int(rsp["server_rx_us32"])
    server_enqueue_us32 = int(rsp["server_enqueue_us32"])
    server_flush_start_us32 = int(rsp["server_flush_start_us32"])
    return {
        "trace_seq": rsp["trace_seq"],
        "rtt_us": us32_delta(recv_us32, client_send_us32),
        "server_rx_to_enqueue_us": us32_delta(server_enqueue_us32, server_rx_us32),
        "server_enqueue_to_flush_start_us": us32_delta(server_flush_start_us32, server_enqueue_us32),
        "recv_us32": recv_us32,
        "recv_epoch_us": recv_epoch_us,
        "server_rx_epoch_us": rsp["server_rx_epoch_us"],
        "server_enqueue_epoch_us": rsp["server_enqueue_epoch_us"],
        "server_flush_start_epoch_us": rsp["server_flush_start_epoch_us"],
        "server_flush_finish_epoch_us": rsp["server_flush_finish_epoch_us"],
        "server_flush_finish_available": bool(rsp["server_flush_finish_us32"]),
        "prev_flush_trace_seq": rsp["prev_flush_trace_seq"],
        "prev_server_flush_finish_available": bool(rsp["prev_server_flush_finish_us32"]),
    }


def monotonic_us32() -> int:
    return int(time.monotonic_ns() // 1000) & 0xFFFFFFFF


def _recv_exact(sock: socket.socket, count: int) -> bytes:
    chunks: list[bytes] = []
    remaining = count
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError("socket closed while reading WebSocket frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def encode_client_frame(payload: bytes, opcode: int = OPCODE_BINARY, mask_key: bytes | None = None) -> bytes:
    if mask_key is None:
        mask_key = os.urandom(4)
    if len(mask_key) != 4:
        raise ValueError("mask_key must be 4 bytes")
    first = 0x80 | (opcode & 0x0F)
    size = len(payload)
    if size < 126:
        header = bytes([first, 0x80 | size])
    elif size <= 0xFFFF:
        header = bytes([first, 0x80 | 126]) + struct.pack("!H", size)
    else:
        header = bytes([first, 0x80 | 127]) + struct.pack("!Q", size)
    masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    return header + mask_key + masked


def read_server_frame(sock: socket.socket) -> tuple[int, bytes]:
    b0, b1 = _recv_exact(sock, 2)
    opcode = b0 & 0x0F
    masked = bool(b1 & 0x80)
    size = b1 & 0x7F
    if size == 126:
        size = struct.unpack("!H", _recv_exact(sock, 2))[0]
    elif size == 127:
        size = struct.unpack("!Q", _recv_exact(sock, 8))[0]
    mask = _recv_exact(sock, 4) if masked else b""
    payload = _recv_exact(sock, size) if size else b""
    if masked:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return opcode, payload


def connect_ws(target: WsTarget, timeout_s: float) -> socket.socket:
    raw = socket.create_connection((target.host, target.port), timeout=timeout_s)
    raw.settimeout(timeout_s)
    if target.scheme == "wss":
        ctx = ssl.create_default_context()
        sock = ctx.wrap_socket(raw, server_hostname=target.server_name)
    else:
        sock = raw

    key = base64.b64encode(os.urandom(16)).decode("ascii")
    host_header = target.host if target.port in (80, 443) else f"{target.host}:{target.port}"
    request = (
        f"GET {target.path} HTTP/1.1\r\n"
        f"Host: {host_header}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "User-Agent: asciicker-fl3800-standalone-lag-probe\r\n"
        "\r\n"
    ).encode("ascii")
    sock.sendall(request)

    response = b""
    while b"\r\n\r\n" not in response:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("socket closed during WebSocket handshake")
        response += chunk
        if len(response) > 16384:
            raise ConnectionError("oversized WebSocket handshake response")
    header_text = response.decode("iso-8859-1", errors="replace")
    if not header_text.startswith("HTTP/1.1 101"):
        raise ConnectionError(f"WebSocket handshake failed: {header_text.splitlines()[0] if header_text else '<empty>'}")
    accept_src = (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
    expected_accept = base64.b64encode(hashlib.sha1(accept_src).digest()).decode("ascii")
    if f"Sec-WebSocket-Accept: {expected_accept}".lower() not in header_text.lower():
        raise ConnectionError("WebSocket handshake missing expected Sec-WebSocket-Accept")
    return sock


def run_probe(target: WsTarget, *, samples: int, interval_s: float, timeout_s: float) -> dict[str, Any]:
    started = time.time()
    rows: list[dict[str, Any]] = []
    with connect_ws(target, timeout_s) as sock:
        for trace_seq in range(1, samples + 1):
            send_us32 = monotonic_us32()
            sock.sendall(encode_client_frame(build_req_lag(trace_seq, send_us32)))
            deadline = time.monotonic() + timeout_s
            rsp = None
            while time.monotonic() < deadline:
                opcode, payload = read_server_frame(sock)
                recv_us32 = monotonic_us32()
                recv_epoch_us = epoch_us()
                if opcode == OPCODE_CLOSE:
                    raise ConnectionError("server closed WebSocket")
                if opcode == OPCODE_PING:
                    sock.sendall(encode_client_frame(payload, OPCODE_PONG))
                    continue
                if opcode != OPCODE_BINARY:
                    continue
                rsp = parse_rsp_lag(payload)
                if rsp and int(rsp["trace_seq"]) == trace_seq:
                    rows.append({**summarize_lag_response(rsp, recv_us32, recv_epoch_us), "raw_size": len(payload)})
                    break
            if rsp is None:
                rows.append({"trace_seq": trace_seq, "timeout": True})
            if trace_seq != samples:
                time.sleep(interval_s)
    rtts = [int(row["rtt_us"]) for row in rows if not row.get("timeout")]
    return {
        "target": target.url,
        "server_name": target.server_name,
        "samples_requested": samples,
        "samples_received": len(rtts),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "rtt_min_us": min(rtts) if rtts else None,
        "rtt_max_us": max(rtts) if rtts else None,
        "rtt_avg_us": (sum(rtts) / len(rtts)) if rtts else None,
        "yellow_count": sum(1 for value in rtts if 100000 <= value < 200000),
        "red_count": sum(1 for value in rtts if value >= 200000),
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, help="ws:// or wss:// URL, e.g. wss://candidate-asciicker.rikiworld.com/ws/y8/")
    parser.add_argument("--server-name", help="TLS SNI name override for wss targets")
    parser.add_argument("--samples", type=int, default=64)
    parser.add_argument("--interval-ms", type=int, default=100)
    parser.add_argument("--timeout-s", type=float, default=5.0)
    parser.add_argument("--out", help="Optional path to write the JSON result artifact")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    target = parse_target(args.target, server_name=args.server_name)
    result = run_probe(target, samples=args.samples, interval_s=args.interval_ms / 1000.0, timeout_s=args.timeout_s)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, sort_keys=True)
            f.write("\n")
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(
            f"{result['target']} samples={result['samples_received']}/{result['samples_requested']} "
            f"rtt_max_us={result['rtt_max_us']} yellow={result['yellow_count']} red={result['red_count']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

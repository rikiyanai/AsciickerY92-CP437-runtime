#!/usr/bin/env python3
"""Join paired browser WebSocket lag rows to Chromium netlog socket events.

FL-3800 uses this only as diagnostic evidence. Public WSS payload bytes remain
TLS-encrypted; this script correlates row send/receive windows to socket byte
events, not to decoded WebSocket trace payloads.

WARNING FL-3800: paired browser netlog was a spent attribution phase. It proved
page/WASM/render/join were not required and exposed aligned/per-connection
lower-layer receive stalls before JS. Do not use netlog-only rows to revive
gameplay/runtime, HUD, analyzer, or JS ownership; current reopen criteria are
contradictory same-run server-span evidence or a true second-vantage change.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict, deque
from pathlib import Path
from typing import Any


def _event_name(types: dict[str, int], value: int) -> str:
    for name, candidate in types.items():
        if candidate == value:
            return name
    return str(value)


def _load_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _dependency_graph(events: list[dict[str, Any]]) -> dict[tuple[int, int], set[tuple[int, int]]]:
    graph: dict[tuple[int, int], set[tuple[int, int]]] = defaultdict(set)
    for event in events:
        source = event.get("source") or {}
        dep = (event.get("params") or {}).get("source_dependency")
        if not dep:
            continue
        src_key = (int(source.get("id")), int(source.get("type")))
        dep_key = (int(dep.get("id")), int(dep.get("type")))
        graph[src_key].add(dep_key)
        graph[dep_key].add(src_key)
    return graph


def _url_request_sources(netlog: dict[str, Any], target: str) -> list[tuple[int, int]]:
    url_request_type = int(netlog["constants"]["logSourceType"]["URL_REQUEST"])
    result: list[tuple[int, int]] = []
    for event in netlog["events"]:
        source = event.get("source") or {}
        params = event.get("params") or {}
        if source.get("type") == url_request_type and params.get("url") == target:
            key = (int(source["id"]), int(source["type"]))
            if key not in result:
                result.append(key)
    return result


def _find_socket_id(netlog: dict[str, Any], target: str) -> int:
    socket_type = int(netlog["constants"]["logSourceType"]["SOCKET"])
    graph = _dependency_graph(netlog["events"])
    starts = _url_request_sources(netlog, target)
    if not starts:
        raise ValueError(f"no URL_REQUEST source found for {target}")
    seen: set[tuple[int, int]] = set()
    queue: deque[tuple[int, int]] = deque(starts)
    sockets: list[int] = []
    while queue:
        key = queue.popleft()
        if key in seen:
            continue
        seen.add(key)
        if key[1] == socket_type:
            sockets.append(key[0])
            continue
        queue.extend(graph.get(key, ()))
    if not sockets:
        raise ValueError(f"no SOCKET source dependency found for {target}")
    return sorted(set(sockets))[-1]


def _socket_events(netlog: dict[str, Any], socket_id: int) -> list[dict[str, Any]]:
    types = netlog["constants"]["logEventTypes"]
    wanted = {
        int(types["SOCKET_BYTES_SENT"]),
        int(types["SOCKET_BYTES_RECEIVED"]),
        int(types.get("SSL_SOCKET_BYTES_SENT", -1)),
        int(types.get("SSL_SOCKET_BYTES_RECEIVED", -1)),
    }
    result = []
    for event in netlog["events"]:
        source = event.get("source") or {}
        if source.get("id") != socket_id or int(event.get("type", -1)) not in wanted:
            continue
        params = event.get("params") or {}
        result.append(
            {
                "time_ms": int(event["time"]),
                "type": _event_name(types, int(event["type"])),
                "byte_count": params.get("byte_count"),
            }
        )
    return result


def _derive_offset_ms(first_row: dict[str, Any], primary_events: list[dict[str, Any]], compare_events: list[dict[str, Any]]) -> float:
    primary_sends = [e["time_ms"] for e in primary_events if e["type"] in ("SOCKET_BYTES_SENT", "SSL_SOCKET_BYTES_SENT")]
    compare_sends = [e["time_ms"] for e in compare_events if e["type"] in ("SOCKET_BYTES_SENT", "SSL_SOCKET_BYTES_SENT")]
    paired: list[int] = []
    for p_time in primary_sends:
        if any(abs(p_time - c_time) <= 5 for c_time in compare_sends):
            paired.append(p_time)
    if not paired:
        raise ValueError("could not find paired socket send events for first row offset")
    return float(min(paired)) - (float(first_row["send_perf_us"]) / 1000.0)


def _events_near(events: list[dict[str, Any]], start_ms: float, end_ms: float) -> list[dict[str, Any]]:
    return [event for event in events if start_ms <= event["time_ms"] <= end_ms]


def _classify_row(primary_rtt: int, compare_rtt: int, threshold_us: int) -> str:
    primary_high = primary_rtt >= threshold_us
    compare_high = compare_rtt >= threshold_us
    if primary_high and compare_high:
        return "aligned_high"
    if primary_high:
        return "primary_only_high"
    if compare_high:
        return "compare_only_high"
    return "not_high"


def correlate(pair: dict[str, Any], netlog: dict[str, Any], *, threshold_us: int = 100_000) -> dict[str, Any]:
    primary_socket = _find_socket_id(netlog, pair["primary_target"])
    compare_socket = _find_socket_id(netlog, pair["compare_target"])
    primary_events = _socket_events(netlog, primary_socket)
    compare_events = _socket_events(netlog, compare_socket)
    offset_ms = _derive_offset_ms(pair["rows"][0], primary_events, compare_events)
    high_rows = []
    for row in pair["rows"]:
        primary = row.get("primary") or {}
        compare = row.get("compare") or {}
        primary_rtt = int(primary.get("rtt_us") or 0)
        compare_rtt = int(compare.get("rtt_us") or 0)
        classification = _classify_row(primary_rtt, compare_rtt, threshold_us)
        if classification == "not_high":
            continue
        send_ms = offset_ms + float(row["send_perf_us"]) / 1000.0
        primary_recv_ms = offset_ms + float(primary.get("recv_perf_us") or 0) / 1000.0
        compare_recv_ms = offset_ms + float(compare.get("recv_perf_us") or 0) / 1000.0
        high_rows.append(
            {
                "trace_seq": row["trace_seq"],
                "classification": classification,
                "send_netlog_ms": round(send_ms, 3),
                "primary_rtt_us": primary_rtt,
                "compare_rtt_us": compare_rtt,
                "primary_socket_receive_delay_ms": round(primary_recv_ms - send_ms, 3),
                "compare_socket_receive_delay_ms": round(compare_recv_ms - send_ms, 3),
                "primary_server_rx_to_enqueue_us": primary.get("server_rx_to_enqueue_us"),
                "primary_server_enqueue_to_flush_start_us": primary.get("server_enqueue_to_flush_start_us"),
                "compare_server_rx_to_enqueue_us": compare.get("server_rx_to_enqueue_us"),
                "compare_server_enqueue_to_flush_start_us": compare.get("server_enqueue_to_flush_start_us"),
                "primary_events": _events_near(primary_events, send_ms - 5, max(primary_recv_ms, compare_recv_ms) + 5),
                "compare_events": _events_near(compare_events, send_ms - 5, max(primary_recv_ms, compare_recv_ms) + 5),
            }
        )
    classification_counts: dict[str, int] = {
        "aligned_high": 0,
        "primary_only_high": 0,
        "compare_only_high": 0,
    }
    for row in high_rows:
        classification_counts[row["classification"]] += 1
    return {
        "primary_target": pair["primary_target"],
        "compare_target": pair["compare_target"],
        "primary_socket_id": primary_socket,
        "compare_socket_id": compare_socket,
        "netlog_offset_ms": offset_ms,
        "threshold_us": threshold_us,
        "classification_counts": classification_counts,
        "high_rows": high_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair-json", required=True)
    parser.add_argument("--netlog", required=True)
    parser.add_argument("--threshold-us", type=int, default=100_000)
    parser.add_argument("--out")
    args = parser.parse_args()

    result = correlate(_load_json(args.pair_json), _load_json(args.netlog), threshold_us=args.threshold_us)
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

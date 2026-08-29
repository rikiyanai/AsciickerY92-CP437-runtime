#!/usr/bin/env python3
"""Correlate paired standalone lag rows with client-side packet timing.

FL-3800 uses this as diagnostic evidence only. TLS payloads are encrypted and
raw TCP packets do not prove WebSocket message identity by themselves; this
script correlates incoming payload timestamps around known REQ_LAG/RSP_LAG high
rows to split "packet reached the client late" from "packet reached the client
but user-space receipt was late".

WARNING FL-3800: client-pcap proof showed raw red rows with retransmit/delayed
packet arrival on the client-visible path after bounded server flush. That
killed repo runtime/server/JS owners for captured rows. Future work here should
tighten packet/window disambiguation or second-vantage attribution only; do not
reopen gameplay/network code from the parked yellow-floor evidence.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


TCPDUMP_RE = re.compile(
    r"^(?P<ts>\d+(?:\.\d+)?)\s+IP6?\s+"
    r"(?P<src>.+)\.(?P<src_port>\d+)\s+>\s+"
    r"(?P<dst>.+)\.(?P<dst_port>\d+):.*\blength\s+(?P<length>\d+)\b"
)


def _load_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _target_port(url: str) -> int:
    parsed = urlparse(url)
    if parsed.port:
        return parsed.port
    if parsed.scheme == "wss":
        return 443
    if parsed.scheme == "ws":
        return 80
    raise ValueError(f"unsupported target scheme in {url!r}")


def _tcpdump_text_from_pcap(path: str | Path) -> str:
    return subprocess.check_output(
        ["tcpdump", "-tt", "-nn", "-r", str(path), "tcp"],
        text=True,
        stderr=subprocess.DEVNULL,
    )


def _read_tcpdump(args: argparse.Namespace) -> str:
    if args.tcpdump_text:
        return Path(args.tcpdump_text).read_text(encoding="utf-8")
    if args.pcap:
        return _tcpdump_text_from_pcap(args.pcap)
    raise ValueError("one of --pcap or --tcpdump-text is required")


def parse_tcpdump_lines(
    text: str,
    *,
    primary_port: int,
    compare_port: int,
    primary_client_port: int | None = None,
    compare_client_port: int | None = None,
) -> dict[str, list[dict[str, Any]]]:
    packets: dict[str, list[dict[str, Any]]] = {"primary": [], "compare": []}
    for line in text.splitlines():
        match = TCPDUMP_RE.search(line.strip())
        if not match:
            continue
        length = int(match.group("length"))
        if length <= 0:
            continue
        src_port = int(match.group("src_port"))
        route = None
        dst_port = int(match.group("dst_port"))
        if src_port == primary_port and (primary_client_port is None or dst_port == primary_client_port):
            route = "primary"
        elif src_port == compare_port and (compare_client_port is None or dst_port == compare_client_port):
            route = "compare"
        if route is None:
            continue
        packets[route].append(
            {
                "time_epoch_us": int(round(float(match.group("ts")) * 1_000_000)),
                "src": match.group("src"),
                "src_port": src_port,
                "dst": match.group("dst"),
                "dst_port": dst_port,
                "length": length,
                "line": line.strip(),
            }
        )
    for route in packets:
        packets[route].sort(key=lambda packet: packet["time_epoch_us"])
    return packets


def _parse_tcpdump_records(text: str) -> list[dict[str, Any]]:
    records = []
    for line in text.splitlines():
        match = TCPDUMP_RE.search(line.strip())
        if not match:
            continue
        records.append(
            {
                "time_epoch_us": int(round(float(match.group("ts")) * 1_000_000)),
                "src_port": int(match.group("src_port")),
                "dst_port": int(match.group("dst_port")),
                "line": line.strip(),
            }
        )
    records.sort(key=lambda packet: packet["time_epoch_us"])
    return records


def _classify(primary_rtt: int | None, compare_rtt: int | None, threshold_us: int) -> str:
    primary_high = primary_rtt is not None and primary_rtt >= threshold_us
    compare_high = compare_rtt is not None and compare_rtt >= threshold_us
    if primary_high and compare_high:
        return "aligned_high"
    if primary_high:
        return "primary_only_high"
    if compare_high:
        return "compare_only_high"
    return "not_high"


def _first_packet(
    packets: list[dict[str, Any]],
    *,
    start_epoch_us: int,
    end_epoch_us: int,
) -> dict[str, Any] | None:
    for packet in packets:
        if start_epoch_us <= packet["time_epoch_us"] <= end_epoch_us:
            return packet
    return None


def _route_row(
    row: dict[str, Any],
    route: str,
    packets: list[dict[str, Any]],
    *,
    search_slack_us: int,
    prompt_us: int,
) -> dict[str, Any]:
    sample = row.get(route) or {}
    if sample.get("timeout"):
        return {"timeout": True, "packet_timing": "timeout"}
    flush_epoch = sample.get("server_flush_start_epoch_us") or sample.get("server_flush_finish_epoch_us")
    recv_epoch = sample.get("recv_epoch_us")
    if not flush_epoch or not recv_epoch:
        return {"packet_timing": "missing_epoch_fields"}
    packet = _first_packet(
        packets,
        start_epoch_us=int(flush_epoch) - search_slack_us,
        end_epoch_us=int(recv_epoch) + search_slack_us,
    )
    if packet is None:
        return {
            "server_flush_start_epoch_us": int(flush_epoch),
            "client_recv_epoch_us": int(recv_epoch),
            "packet_timing": "no_matching_client_packet",
        }
    packet_after_flush_us = int(packet["time_epoch_us"]) - int(flush_epoch)
    recv_after_packet_us = int(recv_epoch) - int(packet["time_epoch_us"])
    if packet_after_flush_us <= prompt_us and recv_after_packet_us > prompt_us:
        timing = "client_receive_or_user_space_scheduling"
    elif packet_after_flush_us > prompt_us:
        timing = "packet_delivery_after_host_egress"
    else:
        timing = "packet_and_user_space_prompt"
    return {
        "server_flush_start_epoch_us": int(flush_epoch),
        "client_packet_epoch_us": int(packet["time_epoch_us"]),
        "client_recv_epoch_us": int(recv_epoch),
        "packet_after_flush_us": packet_after_flush_us,
        "recv_after_packet_us": recv_after_packet_us,
        "packet_length": packet["length"],
        "packet_line": packet["line"],
        "packet_timing": timing,
    }


def correlate(
    pair: dict[str, Any],
    tcpdump_text: str,
    *,
    threshold_us: int = 100_000,
    search_slack_us: int = 10_000,
    prompt_us: int = 20_000,
    primary_port: int | None = None,
    compare_port: int | None = None,
    primary_client_port: int | None = None,
    compare_client_port: int | None = None,
) -> dict[str, Any]:
    primary_port = primary_port or _target_port(pair["primary_target"])
    compare_port = compare_port or _target_port(pair["compare_target"])
    primary_client_port = primary_client_port or pair.get("primary_local_port")
    compare_client_port = compare_client_port or pair.get("compare_local_port")
    packets = parse_tcpdump_lines(
        tcpdump_text,
        primary_port=primary_port,
        compare_port=compare_port,
        primary_client_port=primary_client_port,
        compare_client_port=compare_client_port,
    )
    high_rows = []
    for row in pair.get("rows", []):
        primary = row.get("primary") or {}
        compare = row.get("compare") or {}
        primary_rtt = None if primary.get("timeout") else int(primary.get("rtt_us") or 0)
        compare_rtt = None if compare.get("timeout") else int(compare.get("rtt_us") or 0)
        classification = _classify(primary_rtt, compare_rtt, threshold_us)
        if classification == "not_high":
            continue
        high_rows.append(
            {
                "trace_seq": row.get("trace_seq"),
                "classification": classification,
                "primary_rtt_us": primary_rtt,
                "compare_rtt_us": compare_rtt,
                "primary": _route_row(
                    row,
                    "primary",
                    packets["primary"],
                    search_slack_us=search_slack_us,
                    prompt_us=prompt_us,
                ),
                "compare": _route_row(
                    row,
                    "compare",
                    packets["compare"],
                    search_slack_us=search_slack_us,
                    prompt_us=prompt_us,
                ),
            }
        )
    counts = {"aligned_high": 0, "primary_only_high": 0, "compare_only_high": 0}
    packet_timing_counts: dict[str, int] = {}
    for row in high_rows:
        counts[row["classification"]] += 1
        for route in ("primary", "compare"):
            timing = row[route].get("packet_timing")
            packet_timing_counts[timing] = packet_timing_counts.get(timing, 0) + 1
    return {
        "primary_target": pair["primary_target"],
        "compare_target": pair["compare_target"],
        "primary_port": primary_port,
        "compare_port": compare_port,
        "primary_client_port": primary_client_port,
        "compare_client_port": compare_client_port,
        "threshold_us": threshold_us,
        "search_slack_us": search_slack_us,
        "prompt_us": prompt_us,
        "input_limits": "TCP payload timestamps are correlated by time/port only; encrypted payload identity is not decoded.",
        "packet_counts": {route: len(values) for route, values in packets.items()},
        "classification_counts": counts,
        "packet_timing_counts": packet_timing_counts,
        "high_rows": high_rows,
    }


def build_packet_window_report(
    correlation: dict[str, Any],
    tcpdump_text: str,
    *,
    window_us: int = 250_000,
) -> str:
    """Render bidirectional tcpdump windows around correlated high rows."""
    records = _parse_tcpdump_records(tcpdump_text)
    lines = [
        "FL-3800 client pcap packet windows",
        f"primary_target={correlation['primary_target']} port={correlation['primary_port']} client_port={correlation.get('primary_client_port')}",
        f"compare_target={correlation['compare_target']} port={correlation['compare_port']} client_port={correlation.get('compare_client_port')}",
        f"window_us={window_us}",
        "",
    ]
    for row in correlation.get("high_rows", []):
        lines.append(
            f"trace_seq={row.get('trace_seq')} classification={row.get('classification')} "
            f"primary_rtt_us={row.get('primary_rtt_us')} compare_rtt_us={row.get('compare_rtt_us')}"
        )
        for route in ("primary", "compare"):
            route_data = row.get(route) or {}
            server_port = correlation[f"{route}_port"]
            client_port = correlation.get(f"{route}_client_port")
            packet_line = route_data.get("packet_line")
            if client_port is None and packet_line:
                packet_match = TCPDUMP_RE.search(packet_line)
                if packet_match and int(packet_match.group("src_port")) == server_port:
                    client_port = int(packet_match.group("dst_port"))
            interesting_times = [
                route_data.get("server_flush_start_epoch_us"),
                route_data.get("client_packet_epoch_us"),
                route_data.get("client_recv_epoch_us"),
            ]
            interesting_times = [int(value) for value in interesting_times if value is not None]
            if not interesting_times:
                lines.append(f"  {route}: no packet window; packet_timing={route_data.get('packet_timing')}")
                continue
            start_us = min(interesting_times) - window_us
            end_us = max(interesting_times) + window_us
            lines.append(
                f"  {route}: packet_timing={route_data.get('packet_timing')} "
                f"packet_after_flush_us={route_data.get('packet_after_flush_us')} "
                f"recv_after_packet_us={route_data.get('recv_after_packet_us')} "
                f"window=[{start_us},{end_us}]"
            )
            matched = 0
            for record in records:
                if record["time_epoch_us"] < start_us or record["time_epoch_us"] > end_us:
                    continue
                forward = record["src_port"] == server_port and (
                    client_port is None or record["dst_port"] == client_port
                )
                reverse = record["dst_port"] == server_port and (
                    client_port is None or record["src_port"] == client_port
                )
                if not (forward or reverse):
                    continue
                lines.append(f"    {record['line']}")
                matched += 1
            if matched == 0:
                lines.append("    <no matching tcpdump lines>")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair-json", required=True)
    parser.add_argument("--pcap")
    parser.add_argument("--tcpdump-text")
    parser.add_argument("--threshold-us", type=int, default=100_000)
    parser.add_argument("--search-slack-us", type=int, default=10_000)
    parser.add_argument("--prompt-us", type=int, default=20_000)
    parser.add_argument("--primary-port", type=int)
    parser.add_argument("--compare-port", type=int)
    parser.add_argument("--primary-client-port", type=int)
    parser.add_argument("--compare-client-port", type=int)
    parser.add_argument("--out")
    parser.add_argument("--packet-window-out")
    parser.add_argument("--packet-window-us", type=int, default=250_000)
    args = parser.parse_args()

    tcpdump_text = _read_tcpdump(args)
    result = correlate(
        _load_json(args.pair_json),
        tcpdump_text,
        threshold_us=args.threshold_us,
        search_slack_us=args.search_slack_us,
        prompt_us=args.prompt_us,
        primary_port=args.primary_port,
        compare_port=args.compare_port,
        primary_client_port=args.primary_client_port,
        compare_client_port=args.compare_client_port,
    )
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    if args.packet_window_out:
        packet_window_text = build_packet_window_report(
            result,
            tcpdump_text,
            window_us=args.packet_window_us,
        )
        Path(args.packet_window_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.packet_window_out).write_text(packet_window_text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

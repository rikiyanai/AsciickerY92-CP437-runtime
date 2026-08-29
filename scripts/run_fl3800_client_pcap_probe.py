#!/usr/bin/env python3
"""Run the FL-3800 standalone pair probe with a client-side tcpdump capture.

This is an orchestration helper for the current FL-3800 residual only. It does
not join gameplay and does not change server state. The proof value comes from
the paired probe JSON plus client packet timing correlation.

WARNING FL-3800: managed tcpdump, fail-closed auth handling, --external-pcap,
packet-window output, VPS-local controls, and same-vantage repeats are spent
tooling phases. The lane is parked external/no-repo-owner until contradictory
server-span evidence or true second-vantage attribution changes the owner. Do
not turn pcap wrapper failures into gameplay/runtime/server fixes.
"""
from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import time
from pathlib import Path


DEFAULT_PRIMARY = "wss://candidate-asciicker.rikiworld.com/ws/y8/"
DEFAULT_COMPARE = "ws://35.226.113.14:8080/ws/y8/"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def build_probe_command(args: argparse.Namespace, out_json: Path) -> list[str]:
    return [
        sys.executable,
        str(_repo_root() / "scripts" / "probe_ws_lag_pair.py"),
        "--primary-target",
        args.primary_target,
        "--compare-target",
        args.compare_target,
        "--samples",
        str(args.samples),
        "--interval-ms",
        str(args.interval_ms),
        "--timeout-s",
        str(args.timeout_s),
        "--threshold-us",
        str(args.threshold_us),
        "--out",
        str(out_json),
    ]


def build_analyzer_command(
    args: argparse.Namespace,
    pair_json: Path,
    pcap_path: Path,
    out_json: Path,
    packet_windows: Path,
) -> list[str]:
    return [
        sys.executable,
        str(_repo_root() / "scripts" / "analyze_pair_client_pcap.py"),
        "--pair-json",
        str(pair_json),
        "--pcap",
        str(pcap_path),
        "--threshold-us",
        str(args.threshold_us),
        "--out",
        str(out_json),
        "--packet-window-out",
        str(packet_windows),
    ]


def build_tcpdump_command(args: argparse.Namespace, pcap_path: Path) -> list[str]:
    return [
        *args.tcpdump_cmd,
        "-i",
        args.interface,
        "-w",
        str(pcap_path),
        "tcp port 443 or tcp port 8080",
    ]


def pcap_path_for(args: argparse.Namespace, out_dir: Path) -> Path:
    if args.external_pcap:
        return Path(args.external_pcap)
    return out_dir / "client.pcap"


def _stop_capture(proc: subprocess.Popen[bytes], grace_s: float = 5.0) -> None:
    if proc.poll() is not None:
        return
    proc.send_signal(signal.SIGINT)
    try:
        proc.wait(timeout=grace_s)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=grace_s)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def _write_summary(path: Path, summary: dict[str, object]) -> None:
    path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, object]:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pair_json = out_dir / "public-wss-raw-ws-pair-client-pcap.json"
    pcap_path = pcap_path_for(args, out_dir)
    correlation_json = out_dir / "client-pcap-correlation.json"
    packet_windows = out_dir / "client-pcap-packet-windows.txt"
    summary_path = out_dir / "summary.json"

    tcpdump_cmd = [] if args.external_pcap else build_tcpdump_command(args, pcap_path)
    probe_cmd = build_probe_command(args, pair_json)
    analyzer_cmd = build_analyzer_command(args, pair_json, pcap_path, correlation_json, packet_windows)
    if args.print_commands:
        if tcpdump_cmd:
            print("tcpdump:", " ".join(tcpdump_cmd))
        else:
            print("tcpdump: <external capture>", str(pcap_path))
        print("probe:", " ".join(probe_cmd))
        print("analyze:", " ".join(analyzer_cmd))
    if args.dry_run:
        return {
            "out_dir": str(out_dir),
            "pair_json": str(pair_json),
            "pcap": str(pcap_path),
            "correlation_json": str(correlation_json),
            "packet_windows": str(packet_windows),
            "dry_run": True,
        }

    capture = None
    if not args.external_pcap:
        capture = subprocess.Popen(tcpdump_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        time.sleep(args.capture_warmup_s)
        if capture.poll() is not None:
            stdout, stderr = capture.communicate()
            summary = {
                "status": "blocked",
                "blocker": "tcpdump_start_failed",
                "out_dir": str(out_dir),
                "pair_json": str(pair_json),
                "pcap": str(pcap_path),
                "correlation_json": str(correlation_json),
                "packet_windows": str(packet_windows),
                "tcpdump_returncode": capture.returncode,
                "tcpdump_stdout": stdout.decode(errors="replace"),
                "tcpdump_stderr": stderr.decode(errors="replace"),
                "source_ref": subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip(),
            }
            _write_summary(summary_path, summary)
            raise RuntimeError(
                "tcpdump exited before probe start; "
                f"rc={capture.returncode} stdout={stdout.decode(errors='replace')} "
                f"stderr={stderr.decode(errors='replace')}"
            )
    try:
        subprocess.run(probe_cmd, check=True)
    finally:
        if capture is not None:
            _stop_capture(capture)

    if not pcap_path.exists() or pcap_path.stat().st_size == 0:
        summary = {
            "status": "blocked",
            "blocker": "empty_pcap",
            "out_dir": str(out_dir),
            "pair_json": str(pair_json),
            "pcap": str(pcap_path),
            "correlation_json": str(correlation_json),
            "packet_windows": str(packet_windows),
            "source_ref": subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip(),
        }
        _write_summary(summary_path, summary)
        raise RuntimeError(f"capture did not produce a non-empty pcap: {pcap_path}")
    subprocess.run(analyzer_cmd, check=True)
    correlation = json.loads(correlation_json.read_text(encoding="utf-8"))
    summary = {
        "status": "complete",
        "out_dir": str(out_dir),
        "pair_json": str(pair_json),
        "pcap": str(pcap_path),
        "correlation_json": str(correlation_json),
        "packet_windows": str(packet_windows),
        "classification_counts": correlation.get("classification_counts"),
        "packet_timing_counts": correlation.get("packet_timing_counts"),
        "high_row_count": len(correlation.get("high_rows", [])),
        "capture_mode": "external" if args.external_pcap else "managed",
        "source_ref": subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip(),
    }
    _write_summary(summary_path, summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default=f"/tmp/fl3800-client-pcap-{time.strftime('%Y%m%d-%H%M%S')}")
    parser.add_argument("--primary-target", default=DEFAULT_PRIMARY)
    parser.add_argument("--compare-target", default=DEFAULT_COMPARE)
    parser.add_argument("--samples", type=int, default=1200)
    parser.add_argument("--interval-ms", type=int, default=100)
    parser.add_argument("--timeout-s", type=float, default=5.0)
    parser.add_argument("--threshold-us", type=int, default=100_000)
    parser.add_argument("--interface", default="any")
    parser.add_argument("--tcpdump-cmd", nargs="+", default=["sudo", "-n", "tcpdump"])
    parser.add_argument("--external-pcap", help="Use a pcap written by a separately authorized capture process")
    parser.add_argument("--capture-warmup-s", type=float, default=1.0)
    parser.add_argument("--print-commands", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

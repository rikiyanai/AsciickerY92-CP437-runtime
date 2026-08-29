#!/usr/bin/env python3
"""Candidate-host lag tracing helpers.

Uses remote bpftrace on the candidate host to measure server-side write stall
latency from the real `asciicker-server` process before any further lag-owner
patches are attempted.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent

DEFAULT_SSH_TARGET = "r@35.226.113.14"
DEFAULT_UNIT = "asciicker-server"
DEFAULT_SSH_KEY = Path.home() / ".ssh" / "google_compute_engine"
SSH_CONTROL_PATH = "/tmp/asciicker-ssh-%C"


def resolve_ssh_identity_args() -> list[str]:
    configured = os.environ.get("AK_SSH_KEY") or str(DEFAULT_SSH_KEY)
    if configured and Path(configured).exists():
        return ["-i", configured]
    return []


def shared_ssh_control_args() -> list[str]:
    return [
        "-o",
        "ControlMaster=auto",
        "-o",
        "ControlPersist=60",
        "-o",
        f"ControlPath={SSH_CONTROL_PATH}",
    ]


def ssh_base_cmd(ssh_target: str) -> list[str]:
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ConnectTimeout=10",
        *shared_ssh_control_args(),
        *resolve_ssh_identity_args(),
        ssh_target,
    ]


CANDIDATE_TRACE_DIR = REPO_ROOT / "artifacts" / "maintainer" / "candidate-traces"


def run_ssh(
    ssh_target: str,
    remote_cmd: str,
    *,
    capture: bool = True,
    timeout: int = 120,
    stdin_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*ssh_base_cmd(ssh_target), remote_cmd],
        capture_output=capture,
        text=True,
        input=stdin_text,
        timeout=timeout,
        cwd=str(Path(__file__).resolve().parent.parent),
    )


def require_ok(result: subprocess.CompletedProcess[str], context: str) -> str:
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        detail = stderr or stdout or f"exit {result.returncode}"
        raise RuntimeError(f"{context} failed: {detail}")
    return (result.stdout or "").strip()


def trace_output_or_raise(result: subprocess.CompletedProcess[str], context: str) -> str:
    stdout = (result.stdout or "").strip()
    if result.returncode == 0:
        return stdout
    if stdout and ("@write_count:" in stdout or "@fflush_count:" in stdout):
        return stdout
    stderr = (result.stderr or "").strip()
    detail = stderr or stdout or f"exit {result.returncode}"
    raise RuntimeError(f"{context} failed: {detail}")


def remote_main_pid(ssh_target: str, unit: str) -> int:
    result = run_ssh(
        ssh_target,
        f"systemctl show {shlex.quote(unit)} --property=MainPID --value",
    )
    text = require_ok(result, f"querying {unit} MainPID")
    try:
        pid = int(text)
    except ValueError as exc:
        raise RuntimeError(f"unexpected MainPID output for {unit}: {text!r}") from exc
    if pid <= 0:
        raise RuntimeError(f"{unit} is not running (MainPID={pid})")
    return pid


def remote_libc_path(ssh_target: str) -> str:
    cmd = (
        "python3 - <<'PY'\n"
        "import ctypes.util\n"
        "p = ctypes.util.find_library('c')\n"
        "print(p or '')\n"
        "PY"
    )
    result = run_ssh(ssh_target, cmd)
    text = require_ok(result, "resolving remote libc path").strip()
    if not text:
        raise RuntimeError("unable to resolve remote libc path")
    if text.startswith("/"):
        return text
    for prefix in ("/lib/x86_64-linux-gnu", "/usr/lib/x86_64-linux-gnu", "/lib64", "/usr/lib64"):
        candidate = f"{prefix}/{text}"
        probe = run_ssh(ssh_target, f"test -f {shlex.quote(candidate)} && printf ok")
        if (probe.stdout or "").strip() == "ok":
            return candidate
    raise RuntimeError(f"unable to locate remote libc file for {text!r}")


def remote_main_exe_path(ssh_target: str, unit: str) -> str:
    pid = remote_main_pid(ssh_target, unit)
    result = run_ssh(
        ssh_target,
        f"sudo -n readlink -f /proc/{pid}/exe || readlink -f /proc/{pid}/exe",
    )
    text = require_ok(result, f"resolving {unit} executable path").strip()
    if not text:
        raise RuntimeError(f"unable to resolve executable path for {unit}")
    return text


def remote_symbol_offsets(ssh_target: str, binary_path: str, names: list[str]) -> dict[str, tuple[str, str]]:
    if not names:
        return {}
    joined = ",".join(names)
    remote = (
        "python3 - <<'PY'\n"
        "import json, subprocess, sys\n"
        f"path = {binary_path!r}\n"
        f"targets = {joined!r}.split(',')\n"
        "out = subprocess.check_output(['nm', '-an', path], text=True)\n"
        "matches = {}\n"
        "for line in out.splitlines():\n"
        "    parts = line.split()\n"
        "    if len(parts) < 3:\n"
        "        continue\n"
        "    addr, _typ, sym = parts[0], parts[1], parts[2]\n"
        "    for target in targets:\n"
        "        if target and target in sym and target not in matches:\n"
        "            matches[target] = {'address': addr, 'symbol': sym}\n"
        "print(json.dumps(matches))\n"
        "PY"
    )
    result = run_ssh(ssh_target, remote)
    text = require_ok(result, "resolving remote symbol offsets")
    try:
        payload = json.loads(text)
    except Exception as exc:
        raise RuntimeError(f"invalid remote symbol payload: {text!r}") from exc
    out: dict[str, tuple[str, str]] = {}
    missing: list[str] = []
    for name in names:
        entry = payload.get(name)
        if not isinstance(entry, dict):
            missing.append(name)
            continue
        addr = str(entry.get("address") or "").strip()
        sym = str(entry.get("symbol") or "").strip()
        if not addr or not sym:
            missing.append(name)
            continue
        out[name] = (addr, sym)
    if missing:
        raise RuntimeError(f"unable to resolve remote symbols: {', '.join(missing)}")
    return out


def build_write_trace_script(pid: int, duration_s: int, slow_us: int, stack_depth: int, *, stdio_only: bool) -> str:
    fd_filter = "(args->fd == 1 || args->fd == 2)" if stdio_only else "1"
    slow_stack = f"    @slow_write_ustack[ustack({stack_depth})] = count();\n" if stack_depth > 0 else ""
    return f"""
tracepoint:syscalls:sys_enter_write
/pid == {pid} && {fd_filter}/
{{
  @start_ns[tid] = nsecs;
  @fd[tid] = args->fd;
  @count[tid] = args->count;
}}

tracepoint:syscalls:sys_exit_write
/@start_ns[tid]/
{{
  $us = (nsecs - @start_ns[tid]) / 1000;
  @write_latency_us = hist($us);
  @write_count = count();
  @bytes_total = sum(@count[tid]);
  @fd_count[@fd[tid]] = count();
  @fd_bytes[@fd[tid]] = sum(@count[tid]);
  if ($us >= {slow_us})
  {{
    printf("slow_write us=%llu fd=%d count=%llu ret=%d\\n", $us, @fd[tid], @count[tid], args->ret);
{slow_stack.rstrip()}
    @slow_count = count();
    @slow_fd[@fd[tid]] = count();
  }}
  delete(@start_ns[tid]);
  delete(@fd[tid]);
  delete(@count[tid]);
}}

interval:s:{duration_s}
{{
  exit();
}}
""".strip()


def build_stdio_write_trace_script(pid: int, duration_s: int, slow_us: int) -> str:
    return build_write_trace_script(pid, duration_s, slow_us, 0, stdio_only=True)


def build_fflush_trace_script(libc_path: str, pid: int, duration_s: int, slow_us: int, stack_depth: int) -> str:
    return f"""
uprobe:{libc_path}:fflush
/pid == {pid}/
{{
  @start_ns[tid] = nsecs;
}}

uretprobe:{libc_path}:fflush
/@start_ns[tid]/
{{
  $us = (nsecs - @start_ns[tid]) / 1000;
  @fflush_latency_us = hist($us);
  @fflush_count = count();
  if ($us >= {slow_us})
  {{
    printf("slow_fflush us=%llu ret=%d\\n", $us, retval);
    @slow_fflush_ustack[ustack({stack_depth})] = count();
    @slow_fflush_count = count();
  }}
  delete(@start_ns[tid]);
}}

interval:s:{duration_s}
{{
  exit();
}}
""".strip()


def _sanitize_label(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_").lower() or "func"


def build_function_trace_script(
    binary_path: str,
    pid: int,
    duration_s: int,
    slow_us: int,
    stack_depth: int,
    functions: list[tuple[str, str]],
) -> str:
    blocks: list[str] = []
    for label, symbol in functions:
        key = _sanitize_label(label)
        stack_line = (
            f'    @slow_{key}_ustack[ustack({stack_depth})] = count();\n'
            if stack_depth > 0
            else ""
        )
        blocks.append(
            f"""
uprobe:{binary_path}:{symbol}
/pid == {pid}/
{{
  @{key}_start_ns[tid] = nsecs;
}}

uretprobe:{binary_path}:{symbol}
/@{key}_start_ns[tid]/
{{
  $us = (nsecs - @{key}_start_ns[tid]) / 1000;
  @{key}_latency_us = hist($us);
  @{key}_count = count();
  if ($us >= {slow_us})
  {{
    printf("slow_func name={label} us=%llu\\n", $us);
{stack_line.rstrip()}
    @{key}_slow_count = count();
  }}
  delete(@{key}_start_ns[tid]);
}}
""".strip()
        )
    blocks.append(
        f"""
interval:s:{duration_s}
{{
  exit();
}}
""".strip()
    )
    return "\n\n".join(blocks)


def build_lag_marker_trace_script(
    binary_path: str,
    pid: int,
    duration_s: int,
    marker_symbol: str,
) -> str:
    return f"""
uprobe:{binary_path}:{marker_symbol}
/pid == {pid}/
{{
  @lag_marker_count = count();
  @lag_marker_phase[arg0] = count();
  printf("lag_marker phase=%u trace_seq=%u stamp0=%u stamp1=%u\\n", arg0, arg1, arg2, arg3);
}}

interval:s:{duration_s}
{{
  exit();
}}
""".strip()


def remote_bpftrace(
    ssh_target: str,
    script_text: str,
    *,
    duration_s: int,
) -> subprocess.CompletedProcess[str]:
    remote = (
        "tmp=$(mktemp /tmp/asciicker-trace.XXXXXX.bt) && "
        "cat > \"$tmp\" && "
        f"if sudo -n true >/dev/null 2>&1; then "
        f"sudo -n timeout {duration_s + 5}s bpftrace \"$tmp\"; "
        f"else timeout {duration_s + 5}s bpftrace \"$tmp\"; fi; "
        "rc=$?; rm -f \"$tmp\"; exit $rc"
    )
    return run_ssh(
        ssh_target,
        remote,
        timeout=duration_s + 30,
        stdin_text=script_text,
    )


def default_trace_out_path(stamp: str | None = None) -> Path:
    trace_stamp = stamp or time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    return CANDIDATE_TRACE_DIR / f"stdio-write-{trace_stamp}.txt"


def cmd_check(args: argparse.Namespace) -> int:
    result = run_ssh(
        args.ssh_target,
        "which bpftrace && (sudo -n bpftrace -V || bpftrace -V) && "
        f"systemctl show {shlex.quote(args.unit)} --property=MainPID --value && "
        "(sudo -n journalctl --disk-usage || journalctl --disk-usage)",
    )
    print(require_ok(result, "candidate tracing preflight"))
    return 0


def cmd_trace_stdio_write(args: argparse.Namespace) -> int:
    pid = remote_main_pid(args.ssh_target, args.unit)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    script_text = build_stdio_write_trace_script(
        pid=pid,
        duration_s=args.duration_s,
        slow_us=args.slow_us,
    )
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(
        f"[trace] candidate stdio write latency: unit={args.unit} pid={pid} "
        f"duration_s={args.duration_s} slow_us={args.slow_us} started_at={started_at}"
    )
    result = remote_bpftrace(
        args.ssh_target,
        script_text,
        duration_s=args.duration_s,
    )
    output = trace_output_or_raise(result, "remote bpftrace stdio write trace")
    out_path = Path(args.out).expanduser() if args.out else default_trace_out_path(stamp)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(output + ("\n" if not output.endswith("\n") else ""), encoding="utf-8")
    print(f"[trace] wrote {out_path}")
    print(output)
    return 0


def cmd_trace_write(args: argparse.Namespace) -> int:
    pid = remote_main_pid(args.ssh_target, args.unit)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    script_text = build_write_trace_script(
        pid=pid,
        duration_s=args.duration_s,
        slow_us=args.slow_us,
        stack_depth=args.stack_depth,
        stdio_only=False,
    )
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(
        f"[trace] candidate write latency: unit={args.unit} pid={pid} "
        f"duration_s={args.duration_s} slow_us={args.slow_us} stack_depth={args.stack_depth} started_at={started_at}"
    )
    result = remote_bpftrace(
        args.ssh_target,
        script_text,
        duration_s=args.duration_s,
    )
    output = trace_output_or_raise(result, "remote bpftrace write trace")
    out_path = Path(args.out).expanduser() if args.out else (CANDIDATE_TRACE_DIR / f"write-{stamp}.txt")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(output + ("\n" if not output.endswith("\n") else ""), encoding="utf-8")
    print(f"[trace] wrote {out_path}")
    print(output)
    return 0


def cmd_trace_fflush(args: argparse.Namespace) -> int:
    pid = remote_main_pid(args.ssh_target, args.unit)
    libc_path = args.libc_path or remote_libc_path(args.ssh_target)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    script_text = build_fflush_trace_script(
        libc_path=libc_path,
        pid=pid,
        duration_s=args.duration_s,
        slow_us=args.slow_us,
        stack_depth=args.stack_depth,
    )
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(
        f"[trace] candidate fflush latency: unit={args.unit} pid={pid} libc={libc_path} "
        f"duration_s={args.duration_s} slow_us={args.slow_us} stack_depth={args.stack_depth} started_at={started_at}"
    )
    result = remote_bpftrace(
        args.ssh_target,
        script_text,
        duration_s=args.duration_s,
    )
    output = trace_output_or_raise(result, "remote bpftrace fflush trace")
    out_path = Path(args.out).expanduser() if args.out else (CANDIDATE_TRACE_DIR / f"fflush-{stamp}.txt")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(output + ("\n" if not output.endswith("\n") else ""), encoding="utf-8")
    print(f"[trace] wrote {out_path}")
    print(output)
    return 0


def cmd_trace_funcs(args: argparse.Namespace) -> int:
    pid = remote_main_pid(args.ssh_target, args.unit)
    binary_path = remote_main_exe_path(args.ssh_target, args.unit)
    names = [name.strip() for name in args.functions.split(",") if name.strip()]
    offsets = remote_symbol_offsets(args.ssh_target, binary_path, names)
    functions = [(name, offsets[name][1]) for name in names]
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    script_text = build_function_trace_script(
        binary_path=binary_path,
        pid=pid,
        duration_s=args.duration_s,
        slow_us=args.slow_us,
        stack_depth=args.stack_depth,
        functions=functions,
    )
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(
        f"[trace] candidate function latency: unit={args.unit} pid={pid} binary={binary_path} "
        f"functions={','.join(name for name, _ in functions)} duration_s={args.duration_s} "
        f"slow_us={args.slow_us} stack_depth={args.stack_depth} started_at={started_at}"
    )
    result = remote_bpftrace(
        args.ssh_target,
        script_text,
        duration_s=args.duration_s,
    )
    output = trace_output_or_raise(result, "remote bpftrace function trace")
    out_path = Path(args.out).expanduser() if args.out else (CANDIDATE_TRACE_DIR / f"funcs-{stamp}.txt")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(output + ("\n" if not output.endswith("\n") else ""), encoding="utf-8")
    print(f"[trace] wrote {out_path}")
    print(output)
    return 0


def cmd_trace_lag_markers(args: argparse.Namespace) -> int:
    pid = remote_main_pid(args.ssh_target, args.unit)
    binary_path = remote_main_exe_path(args.ssh_target, args.unit)
    offsets = remote_symbol_offsets(args.ssh_target, binary_path, [args.symbol])
    marker_symbol = offsets[args.symbol][1]
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    script_text = build_lag_marker_trace_script(
        binary_path=binary_path,
        pid=pid,
        duration_s=args.duration_s,
        marker_symbol=marker_symbol,
    )
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(
        f"[trace] candidate lag markers: unit={args.unit} pid={pid} binary={binary_path} "
        f"symbol={marker_symbol} duration_s={args.duration_s} started_at={started_at}"
    )
    result = remote_bpftrace(
        args.ssh_target,
        script_text,
        duration_s=args.duration_s,
    )
    output = trace_output_or_raise(result, "remote bpftrace lag marker trace")
    out_path = Path(args.out).expanduser() if args.out else (CANDIDATE_TRACE_DIR / f"lag-markers-{stamp}.txt")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(output + ("\n" if not output.endswith("\n") else ""), encoding="utf-8")
    print(f"[trace] wrote {out_path}")
    print(output)
    return 0


def cmd_vacuum_journal(args: argparse.Namespace) -> int:
    before = require_ok(
        run_ssh(
            args.ssh_target,
            "sudo -n journalctl --disk-usage || journalctl --disk-usage",
        ),
        "checking remote journal size",
    )
    print(f"[journal] before: {before}")
    result = run_ssh(
        args.ssh_target,
        f"sudo -n journalctl --vacuum-size={shlex.quote(args.size)} || journalctl --vacuum-size={shlex.quote(args.size)}",
        timeout=180,
    )
    print(require_ok(result, "vacuuming remote journal"))
    after = require_ok(
        run_ssh(
            args.ssh_target,
            "sudo -n journalctl --disk-usage || journalctl --disk-usage",
        ),
        "checking remote journal size after vacuum",
    )
    print(f"[journal] after: {after}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Trace candidate-host lag owners with remote bpftrace."
    )
    parser.set_defaults(func=None)
    parser.add_argument("--ssh-target", default=DEFAULT_SSH_TARGET)
    parser.add_argument("--unit", default=DEFAULT_UNIT)
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="Verify remote bpftrace access and journal size.")
    check.set_defaults(func=cmd_check)

    trace_stdio = sub.add_parser(
        "trace-stdio-write",
        help="Histogram stdout/stderr write latency for the remote asciicker-server process.",
    )
    trace_stdio.add_argument("--duration-s", type=int, default=15)
    trace_stdio.add_argument("--slow-us", type=int, default=5000)
    trace_stdio.add_argument("--out", default="")
    trace_stdio.set_defaults(func=cmd_trace_stdio_write)

    trace_write = sub.add_parser(
        "trace-write",
        help="Histogram all write() syscall latency for the remote asciicker-server process and capture slow-call stacks.",
    )
    trace_write.add_argument("--duration-s", type=int, default=15)
    trace_write.add_argument("--slow-us", type=int, default=5000)
    trace_write.add_argument("--stack-depth", type=int, default=8)
    trace_write.add_argument("--out", default="")
    trace_write.set_defaults(func=cmd_trace_write)

    trace_fflush = sub.add_parser(
        "trace-fflush",
        help="Histogram remote libc fflush latency and capture slow-call user stacks.",
    )
    trace_fflush.add_argument("--duration-s", type=int, default=15)
    trace_fflush.add_argument("--slow-us", type=int, default=5000)
    trace_fflush.add_argument("--stack-depth", type=int, default=8)
    trace_fflush.add_argument("--libc-path", default="")
    trace_fflush.add_argument("--out", default="")
    trace_fflush.set_defaults(func=cmd_trace_fflush)

    trace_funcs = sub.add_parser(
        "trace-funcs",
        help="Histogram remote server function latency for named symbols and capture slow-call stacks.",
    )
    trace_funcs.add_argument("--duration-s", type=int, default=15)
    trace_funcs.add_argument("--slow-us", type=int, default=5000)
    trace_funcs.add_argument("--stack-depth", type=int, default=8)
    trace_funcs.add_argument(
        "--functions",
        default="SvrFlushEvents,SvrPublishAuthoritativeState,ServerTick",
        help="Comma-separated raw symbol substrings to trace on the remote server binary.",
    )
    trace_funcs.add_argument("--out", default="")
    trace_funcs.set_defaults(func=cmd_trace_funcs)

    trace_lag_markers = sub.add_parser(
        "trace-lag-markers",
        help="Print per-phase AKLagTraceBpfMarker calls for lag echo correlation.",
    )
    trace_lag_markers.add_argument("--duration-s", type=int, default=10)
    trace_lag_markers.add_argument(
        "--symbol",
        default="AKLagTraceBpfMarker",
        help="Raw symbol substring for the server's lag marker hook.",
    )
    trace_lag_markers.add_argument("--out", default="")
    trace_lag_markers.set_defaults(func=cmd_trace_lag_markers)

    vacuum = sub.add_parser(
        "vacuum-journal",
        help="Vacuum the remote journal to a bounded size before proof runs.",
    )
    vacuum.add_argument("--size", default="200M")
    vacuum.set_defaults(func=cmd_vacuum_journal)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

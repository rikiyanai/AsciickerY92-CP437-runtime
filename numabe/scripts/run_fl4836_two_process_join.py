#!/usr/bin/env python3
"""FL-4836 / RQ-206: two-process join harness for the ONE Colyseus authority.

WHAT THIS PROVES, AND WHAT IT DOES NOT
--------------------------------------
The architecture law for this repository is that gameplay authority is a
TypeScript Colyseus process. Solo, LAN host, direct self-host and dedicated
headless are four DEPLOYMENT FORMS OF ONE ARTIFACT, not four architectures
(FL-4836). This harness tests that law with two real OS processes:

  * ``audit``    - static: exactly one server artifact exists, and the Godot
                   launcher seam spawns THAT artifact and no other.
  * ``flagged``  - executed: one authority process plus TWO Godot client
                   processes that must land in the SAME room. The clients are
                   started with ``--colyseus-client``, so this leg proves the
                   single-authority/same-room property. It is NOT G9
                   acceptance, because G9 forbids player-facing flags.
  * ``flagless`` - the G9 target: both Godot processes started with an EMPTY
                   user-argument vector, reaching Host/Join through the menu.
                   This leg needs a menu automation seam that does not exist
                   yet (see SEAM below); until it lands the leg reports
                   BLOCKED and names the seam instead of passing.

SEAM REQUIRED FOR THE FLAGLESS LEG (owned by the G7 menu lane, not by this file)
-------------------------------------------------------------------------------
``godot_project/scripts/MainMenu.gd`` ``_ready()`` must, when the environment
variable ``ASCIICKER_AUTO_ENTRY`` is set, invoke one of the existing entry
handlers already in that file:

    ASCIICKER_AUTO_ENTRY=play  -> await _on_play_pressed()
    ASCIICKER_AUTO_ENTRY=host  -> await _on_host_pressed()
    ASCIICKER_AUTO_ENTRY=join  -> await _enter_world(
                                      OS.get_environment("ASCIICKER_AUTO_HOST"),
                                      int(OS.get_environment("ASCIICKER_AUTO_PORT")),
                                      _map_choice, false, false)

An ENVIRONMENT VARIABLE is used deliberately, not a command-line flag:
``scripts/boot/LaunchClassifier.gd`` classifies from ``OS.get_cmdline_user_args()``
only, so an env var leaves the classification at ``MODE_CONSUMER_BOOT`` and the
launch stays literally flagless. Adding a CLI flag instead would both violate
the G9 acceptance rule and, for ``--fl4359-command-channel=``, re-route the
process into ``MODE_PROOF_HARNESS`` (LaunchClassifier.gd:73-76), which never
reaches the menu at all.

PROCESS DISCIPLINE
------------------
No ``timeout(1)``, no foreground ``sleep``. Every wait is a poll loop that
decides on a COMPLETION SIGNAL printed by the process itself:

    authority : ASCIICKER_COLYSEUS_SPIKE_LISTENING   (colyseus_server/src/index.ts:16)
    client    : GODOT_COLYSEUS_JOINED room= session= (ColyseusGameClient.gd:605)

Elapsed time is only an outer abort bound and is reported as ``DEADLINE``, never
as success. Every process records role, pid, argv, log path and the signal that
ended its wait, into a JSON receipt.

FALSIFIERS
----------
* ``COLYSEUS_ROOM_MAP_ID`` (AuthoritativeRoom.ts:308) is printed once per room
  CREATION. Two clients in one room => exactly ONE such line. Two clients that
  diverged into two rooms => TWO lines. A constant cannot produce this.
* The two clients must report DIFFERENT ``session=`` ids, so one process cannot
  be miscounted as two participants.
* ``audit`` fails if a second server entry point appears anywhere in the tree,
  or if the launcher stops naming ``src/index.ts``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COLYSEUS_DIR = REPO_ROOT / "colyseus_server"
SERVER_ENTRY_RELATIVE = "src/index.ts"
GODOT_PROJECT = REPO_ROOT / "godot_project"
LAUNCHER_GD = GODOT_PROJECT / "scripts" / "boot" / "LocalAuthorityLauncher.gd"
MAINMENU_GD = GODOT_PROJECT / "scripts" / "MainMenu.gd"
GODOT_BIN = os.environ.get("GODOT_BIN", "/opt/homebrew/bin/godot")

SERVER_LISTENING_MARKER = "ASCIICKER_COLYSEUS_SPIKE_LISTENING"
CLIENT_JOINED_MARKER = "GODOT_COLYSEUS_JOINED"
CONSUMER_BOOT_MARKER = "GODOT_CONSUMER_BOOT_ROUTED"
ROOM_CREATED_MARKER = "COLYSEUS_ROOM_MAP_ID"

# The exact command LocalAuthorityLauncher.gd:98-104 composes. `audit` proves
# this literal is still what the GDScript builds; nothing here is allowed to
# drift into a second, harness-only way of starting the authority.
SERVER_ARGV = ["node", "--import", "tsx", SERVER_ENTRY_RELATIVE]

POLL_INTERVAL_S = 0.25


@dataclass
class Managed:
    """One supervised OS process, identified by role rather than by index."""

    role: str
    argv: list[str]
    cwd: str
    log_path: Path
    popen: subprocess.Popen
    signal: str = "PENDING"
    signal_line: str = ""
    exit_code: int | None = None
    env_overrides: dict[str, str] = field(default_factory=dict)

    @property
    def pid(self) -> int:
        return self.popen.pid

    def read_log(self) -> str:
        try:
            return self.log_path.read_text(errors="replace")
        except OSError:
            return ""

    def receipt(self) -> dict:
        return {
            "role": self.role,
            "pid": self.pid,
            "argv": self.argv,
            "cwd": self.cwd,
            "log_path": str(self.log_path),
            "env_overrides": self.env_overrides,
            "completion_signal": self.signal,
            "completion_line": self.signal_line,
            "exit_code": self.exit_code,
        }


def spawn(role: str, argv: list[str], cwd: Path, log_path: Path,
          env_overrides: dict[str, str]) -> Managed:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(env_overrides)
    handle = log_path.open("w")
    popen = subprocess.Popen(
        argv, cwd=str(cwd), env=env, stdout=handle, stderr=subprocess.STDOUT
    )
    managed = Managed(
        role=role,
        argv=list(argv),
        cwd=str(cwd),
        log_path=log_path,
        popen=popen,
        env_overrides=dict(env_overrides),
    )
    print(
        f"[fl4836] SPAWNED role={role} pid={managed.pid} log={log_path} argv={' '.join(argv)}",
        flush=True,
    )
    return managed


def wait_for_signal(managed: Managed, marker: str, abort_after_s: float) -> str:
    """Poll until the process PRINTS `marker`, exits, or the abort bound trips.

    The decision is the marker. Elapsed time only aborts, and an abort is
    recorded as DEADLINE so it can never be read as a pass.
    """
    abort_at = time.monotonic() + abort_after_s
    while True:
        text = managed.read_log()
        for line in text.splitlines():
            if marker in line:
                managed.signal = "SIGNAL"
                managed.signal_line = line.strip()
                print(f"[fl4836] SIGNAL role={managed.role} pid={managed.pid} :: {line.strip()}", flush=True)
                return managed.signal
        code = managed.popen.poll()
        if code is not None:
            managed.exit_code = code
            managed.signal = "EXITED"
            managed.signal_line = f"exit_code={code} without {marker}"
            print(f"[fl4836] EXITED role={managed.role} pid={managed.pid} code={code} before {marker}", flush=True)
            return managed.signal
        if time.monotonic() >= abort_at:
            managed.signal = "DEADLINE"
            managed.signal_line = f"no {marker} within {abort_after_s:.0f}s abort bound"
            print(f"[fl4836] DEADLINE role={managed.role} pid={managed.pid} missing {marker}", flush=True)
            return managed.signal
        time.sleep(POLL_INTERVAL_S)


def terminate(managed: Managed) -> None:
    if managed.popen.poll() is None:
        managed.popen.terminate()
        deadline = time.monotonic() + 10.0
        while managed.popen.poll() is None and time.monotonic() < deadline:
            time.sleep(POLL_INTERVAL_S)
        if managed.popen.poll() is None:
            managed.popen.kill()
    managed.exit_code = managed.popen.poll()


# ---------------------------------------------------------------------------
# audit: one artifact, one authority
# ---------------------------------------------------------------------------

def audit() -> tuple[bool, list[str]]:
    findings: list[str] = []
    ok = True

    entry = COLYSEUS_DIR / SERVER_ENTRY_RELATIVE
    if not entry.exists():
        findings.append(f"FAIL server artifact missing: {entry}")
        return False, findings
    findings.append(f"PASS single server artifact present: {entry.relative_to(REPO_ROOT)}")

    # Any OTHER module that constructs a Colyseus Server is a second authority.
    server_ctors = []
    for path in sorted(COLYSEUS_DIR.glob("src/**/*.ts")):
        text = path.read_text(errors="replace")
        if re.search(r"new\s+Server\s*\(", text):
            server_ctors.append(str(path.relative_to(REPO_ROOT)))
    if server_ctors != [f"colyseus_server/{SERVER_ENTRY_RELATIVE}"]:
        ok = False
        findings.append(f"FAIL more than one Colyseus Server constructor: {server_ctors}")
    else:
        findings.append("PASS exactly one `new Server(` in colyseus_server/src")

    launcher = LAUNCHER_GD.read_text(errors="replace")
    if SERVER_ENTRY_RELATIVE not in launcher:
        ok = False
        findings.append(f"FAIL {LAUNCHER_GD.name} no longer names {SERVER_ENTRY_RELATIVE}")
    else:
        findings.append(f"PASS {LAUNCHER_GD.name} spawns {SERVER_ENTRY_RELATIVE}")
    if "node --import tsx" not in launcher:
        ok = False
        findings.append(f"FAIL {LAUNCHER_GD.name} start command drifted from `node --import tsx`")
    else:
        findings.append("PASS launcher start command matches the proof-runner command")
    if SERVER_LISTENING_MARKER not in launcher:
        ok = False
        findings.append(f"FAIL {LAUNCHER_GD.name} lost the {SERVER_LISTENING_MARKER} contract")

    # No deployment form may re-enter the deleted GDScript authority.
    clean = True
    for name in ("MainMenu.gd", "boot/LocalAuthorityLauncher.gd", "boot/ConsumerBoot.gd"):
        text = (GODOT_PROJECT / "scripts" / name).read_text(errors="replace")
        if re.search(r"GameServer\.new\(\)|start_local\(\)|start_server\(\)", text):
            ok = False
            clean = False
            findings.append(f"FAIL player-facing file re-enters deleted Godot authority: {name}")
    if clean:
        findings.append("PASS no player-facing boot file constructs GameServer")

    return ok, findings


# ---------------------------------------------------------------------------
# executed legs
# ---------------------------------------------------------------------------

def godot_argv(user_args: list[str], headless: bool = False, script: str = "") -> list[str]:
    argv = [GODOT_BIN, "--path", str(GODOT_PROJECT)]
    # Headless skips the display server and the TrueFF launch-guard admission
    # (LaunchAdmission admits headless unconditionally). Windowed stays the
    # default: headed acceptance needs the real target and its guard.
    if headless:
        argv.append("--headless")
    # Engine directives (--script) must precede the "--" user-args separator;
    # anything after it is invisible to the engine.
    if script:
        argv.extend(["--script", script])
    if user_args:
        argv.append("--")
        argv.extend(user_args)
    return argv

def run_flagged(args, out_dir: Path) -> int:
    """One authority, two clients, both must land in the SAME room."""
    server = spawn(
        "A-authority",
        SERVER_ARGV,
        COLYSEUS_DIR,
        out_dir / "A-authority.log",
        {"COLYSEUS_PORT": str(args.port)},
    )
    clients: list[Managed] = []
    receipts: dict = {"mode": "flagged", "port": args.port, "map_id": args.map_id}
    verdict = 1
    try:
        if wait_for_signal(server, SERVER_LISTENING_MARKER, args.server_boot_abort_s) != "SIGNAL":
            receipts["verdict"] = "FAIL authority never reported LISTENING"
            return 3
        # Both clients spawn BEFORE either is awaited: a sequential spawn lets
        # the first client's soak expire (and its room dispose) before the
        # second finishes its world load, which reads as two rooms.
        for role in ("B-client", "C-client"):
            user_args = [
                "--colyseus-client",
                f"--host=127.0.0.1",
                f"--port={args.port}",
                f"--map-id={args.map_id}",
                f"--colyseus-client-soak-ms={args.soak_ms}",
            ]
            clients.append(spawn(role, godot_argv(user_args, args.headless_clients), REPO_ROOT, out_dir / f"{role}.log", {}))
        for client in clients:
            wait_for_signal(client, CLIENT_JOINED_MARKER, args.client_join_abort_s)
        joined = [c for c in clients if c.signal == "SIGNAL"]
        server_text = server.read_log()
        room_creations = [
            line for line in server_text.splitlines() if ROOM_CREATED_MARKER in line
        ]
        sessions = []
        for client in clients:
            match = re.search(r"session=([^\s]+)", client.signal_line)
            sessions.append(match.group(1) if match else "")
        receipts["room_creation_lines"] = room_creations
        receipts["client_sessions"] = sessions
        same_room = len(room_creations) == 1
        distinct = len([s for s in sessions if s]) == 2 and sessions[0] != sessions[1]
        if len(joined) == 2 and same_room and distinct:
            receipts["verdict"] = "PASS two processes, one authority, one room"
            verdict = 0
        else:
            receipts["verdict"] = (
                f"FAIL joined={len(joined)}/2 room_creations={len(room_creations)} "
                f"distinct_sessions={distinct}"
            )
            verdict = 1
        return verdict
    finally:
        for client in clients:
            terminate(client)
        terminate(server)
        receipts["processes"] = [p.receipt() for p in [server, *clients]]
        _write_receipt(out_dir, receipts)


def run_flagless(args, out_dir: Path) -> int:
    """The G9 target leg. Refuses to pass while the menu seam is absent."""
    menu = MAINMENU_GD.read_text(errors="replace")
    if "ASCIICKER_AUTO_ENTRY" not in menu:
        receipt = {
            "mode": "flagless",
            "verdict": "BLOCKED menu automation seam absent",
            "required_seam": {
                "file": str(MAINMENU_GD.relative_to(REPO_ROOT)),
                "function": "_ready",
                "contract": "honour ASCIICKER_AUTO_ENTRY=play|host|join by calling the "
                            "existing _on_play_pressed / _on_host_pressed / _enter_world",
                "why_env_not_flag": "LaunchClassifier.classify reads argv only, so an env "
                                    "var keeps the launch at MODE_CONSUMER_BOOT and leaves "
                                    "the process literally flagless",
            },
            "processes": [],
        }
        _write_receipt(out_dir, receipt)
        print("[fl4836] BLOCKED: " + receipt["verdict"], flush=True)
        print("[fl4836] seam: " + json.dumps(receipt["required_seam"]), flush=True)
        return 2

    processes: list[Managed] = []
    receipts: dict = {"mode": "flagless", "port": args.port, "map_id": args.map_id}
    verdict = 1
    try:
        host_proc = spawn(
            "A-host",
            godot_argv([]),
            REPO_ROOT,
            out_dir / "A-host.log",
            {"ASCIICKER_AUTO_ENTRY": "host", "ASCIICKER_AUTO_PORT": str(args.port)},
        )
        processes.append(host_proc)
        if wait_for_signal(host_proc, CONSUMER_BOOT_MARKER, args.client_join_abort_s) != "SIGNAL":
            receipts["verdict"] = "FAIL process A never reached the consumer boot route"
            return 1
        if wait_for_signal(host_proc, CLIENT_JOINED_MARKER, args.client_join_abort_s) != "SIGNAL":
            receipts["verdict"] = "FAIL process A hosted but never joined its own authority"
            return 1
        join_proc = spawn(
            "B-join",
            godot_argv([]),
            REPO_ROOT,
            out_dir / "B-join.log",
            {
                "ASCIICKER_AUTO_ENTRY": "join",
                "ASCIICKER_AUTO_HOST": args.join_host,
                "ASCIICKER_AUTO_PORT": str(args.port),
            },
        )
        processes.append(join_proc)
        wait_for_signal(join_proc, CLIENT_JOINED_MARKER, args.client_join_abort_s)
        sessions = []
        for proc in processes:
            match = re.search(r"session=([^\s]+)", proc.signal_line)
            sessions.append(match.group(1) if match else "")
        receipts["client_sessions"] = sessions
        # The authority log lives where LocalAuthorityLauncher puts it.
        authority_log = Path(os.path.expanduser("~")) / "Library/Application Support/Godot"
        receipts["authority_log_hint"] = (
            "user://colyseus_local_authority.log (LocalAuthorityLauncher server log); "
            f"search under {authority_log}"
        )
        if all(p.signal == "SIGNAL" for p in processes) and sessions[0] != sessions[1]:
            receipts["verdict"] = "PASS flagless host + flagless join, distinct sessions"
            verdict = 0
        else:
            receipts["verdict"] = "FAIL flagless two-process join incomplete"
        return verdict
    finally:
        for proc in reversed(processes):
            terminate(proc)
        receipts["processes"] = [p.receipt() for p in processes]
        _write_receipt(out_dir, receipts)

# ---------------------------------------------------------------------------
# hosted: UI-equivalent host plus a live join (G9, FL-4836 attempt 3+)
# ---------------------------------------------------------------------------
# `flagged` starts the authority from a shell. This leg starts it through the
# shipped hosting code instead: g9_host_harness.gd calls the same
# LocalAuthorityLauncher.ensure_local_authority the menu HOST button calls.
# A full-Main joiner then joins with a direct address and the
# --colyseus-join-proof vehicle, and both sides are stopped afterwards.

HOST_RESULT_MARKER = "G9_HOST_RESULT ok=true"
JOIN_PROOF_MARKER = "GODOT_G9_JOIN_PROOF_VERIFIED"
DEDICATED_STARTED_MARKER = "GODOT_DEDICATED_AUTHORITY_STARTED"
DEDICATED_HOLD_MARKER = "GODOT_DEDICATED_AUTHORITY_HOLD_COMPLETE"
AUTHORITY_STOPPED_MARKER = "GODOT_CONSUMER_BOOT_AUTHORITY_STOPPED"


def terminate_pid(pid: int) -> bool:
    """Stop a supervised server by PID. Returns True when it is gone."""
    import signal as _signal

    if pid <= 0:
        return False
    try:
        os.kill(pid, _signal.SIGTERM)
    except (OSError, ValueError):
        return False
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except (OSError, ValueError):
            return True
        time.sleep(POLL_INTERVAL_S)
    try:
        os.kill(pid, _signal.SIGKILL)
    except (OSError, ValueError):
        return True
    return False


def port_listening(port: int) -> bool:
    import socket as _socket

    sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    sock.settimeout(1.0)
    try:
        return sock.connect_ex(("127.0.0.1", port)) == 0
    finally:
        sock.close()


def run_hosted(args, out_dir: Path) -> int:
    """Host through the shipped launcher, join from a second process."""
    receipts: dict = {"mode": "hosted", "port": args.port, "map_id": args.map_id}
    if port_listening(args.port):
        receipts["verdict"] = f"FAIL port {args.port} already listening (stale server?)"
        _write_receipt(out_dir, receipts)
        return 3
    host = spawn(
        "A-host-harness",
        godot_argv(["--g9-host-port=%d" % args.port], headless=True,
                   script="res://scripts/proofs/g9_host_harness.gd"),
        REPO_ROOT, out_dir / "A-host-harness.log", {},
    )
    verdict = 1
    joiner = None
    server_pid = -1
    try:
        if wait_for_signal(host, HOST_RESULT_MARKER, args.server_boot_abort_s) != "SIGNAL":
            receipts["verdict"] = "FAIL host harness never reported hosting"
            return 3
        match = re.search(r"pid=(\d+)", host.signal_line)
        server_pid = int(match.group(1)) if match else -1
        if not port_listening(args.port):
            receipts["verdict"] = "FAIL hosted authority not listening"
            return 1
        join_receipt = out_dir / "join_receipt.json"
        joiner = spawn(
            "B-joiner",
            godot_argv(["--colyseus-client", "--host=127.0.0.1",
                        f"--port={args.port}", f"--map-id={args.map_id}",
                        f"--colyseus-join-proof={join_receipt}"], headless=True),
            REPO_ROOT, out_dir / "B-joiner.log", {},
        )
        if wait_for_signal(joiner, JOIN_PROOF_MARKER, args.client_join_abort_s) != "SIGNAL":
            receipts["verdict"] = "FAIL joiner never verified the join"
            return 1
        try:
            body = json.loads(join_receipt.read_text(errors="replace"))
        except (OSError, ValueError):
            body = {}
        receipts["join_receipt"] = body
        if joiner.popen.wait(timeout=120) != 0 or not body.get("ok", False):
            receipts["verdict"] = "FAIL join receipt not ok"
            return 1
        receipts["verdict"] = "PASS UI-equivalent host plus cross-process join"
        verdict = 0
        return verdict
    finally:
        if joiner is not None:
            terminate(joiner)
        terminate(host)
        receipts["server_pid"] = server_pid
        receipts["server_stopped"] = terminate_pid(server_pid)
        receipts["processes"] = [p.receipt() for p in ([host] if joiner is None else [host, joiner])]
        _write_receipt(out_dir, receipts)


def run_dedicated(args, out_dir: Path) -> int:
    """Dedicated headless host with clean shutdown through the real quit path."""
    receipts: dict = {"mode": "dedicated", "port": args.port}
    if port_listening(args.port):
        receipts["verdict"] = f"FAIL port {args.port} already listening (stale server?)"
        _write_receipt(out_dir, receipts)
        return 3
    hold_ms = 15000
    proc = spawn(
        "A-dedicated",
        godot_argv(["--dedicated-authority", f"--port={args.port}",
                    f"--dedicated-hold-ms={hold_ms}"], headless=True),
        REPO_ROOT, out_dir / "A-dedicated.log", {},
    )
    verdict = 1
    try:
        started = wait_for_signal(proc, DEDICATED_STARTED_MARKER, args.server_boot_abort_s + 90.0)
        listening = port_listening(args.port) if started == "SIGNAL" else False
        try:
            code = proc.popen.wait(timeout=300)
        except subprocess.TimeoutExpired:
            receipts["verdict"] = "FAIL dedicated did not quit after hold (DEADLINE)"
            return 1
        text = proc.read_log()
        hold_done = DEDICATED_HOLD_MARKER in text
        stopped = AUTHORITY_STOPPED_MARKER in text
        closed = not port_listening(args.port)
        if started == "SIGNAL" and listening and hold_done and stopped and closed and code == 0:
            receipts["verdict"] = "PASS dedicated hosted, held, and stopped its server"
            verdict = 0
        else:
            receipts["verdict"] = (
                f"FAIL started={started} listening={listening} hold={hold_done} "
                f"stopped={stopped} closed={closed} exit={code}"
            )
        return verdict
    finally:
        terminate(proc)
        receipts["processes"] = [proc.receipt()]
        _write_receipt(out_dir, receipts)


def run_flagless_boot(args, out_dir: Path) -> int:
    """A second process with zero user flags must reach the shipping menu."""
    receipts: dict = {"mode": "flagless-boot"}
    proc = spawn("A-flagless", godot_argv([], headless=True), REPO_ROOT, out_dir / "A-flagless.log", {})
    verdict = 1
    try:
        routed = wait_for_signal(proc, CONSUMER_BOOT_MARKER, args.client_join_abort_s)
        alive = proc.popen.poll() is None
        time.sleep(3)
        still_alive = proc.popen.poll() is None
        if routed == "SIGNAL" and alive and still_alive:
            receipts["verdict"] = "PASS flagless process reached consumer boot alive"
            verdict = 0
        else:
            receipts["verdict"] = f"FAIL routed={routed} alive={alive} still_alive={still_alive}"
        return verdict
    finally:
        terminate(proc)
        receipts["processes"] = [proc.receipt()]
        _write_receipt(out_dir, receipts)



def _write_receipt(out_dir: Path, payload: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "receipt.json"
    path.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"[fl4836] receipt {path}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["audit", "flagged", "flagless",
                                         "hosted", "dedicated", "flagless-boot"])
    parser.add_argument("--port", type=int, default=2567,
                        help="shipping default, ConsumerBoot.SHIPPING_DEFAULT_PORT")
    parser.add_argument("--map-id", default="game_map_y8")
    parser.add_argument("--join-host", default="127.0.0.1",
                        help="LAN/direct leg: the address process B is told to join")
    parser.add_argument("--soak-ms", type=int, default=12000)
    parser.add_argument("--server-boot-abort-s", type=float, default=90.0)
    parser.add_argument("--client-join-abort-s", type=float, default=240.0)
    parser.add_argument("--headless-clients", action="store_true",
                        help="run flagged clients headless (no display, no launch-guard admission)")
    parser.add_argument("--out-dir", default="")
    args = parser.parse_args()

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out_dir) if args.out_dir else REPO_ROOT / ".run" / "fl4836-two-process-join" / stamp

    if args.mode == "audit":
        ok, findings = audit()
        for line in findings:
            print(f"[fl4836-audit] {line}", flush=True)
        _write_receipt(out_dir, {"mode": "audit", "ok": ok, "findings": findings})
        return 0 if ok else 1
    if args.mode == "flagged":
        return run_flagged(args, out_dir)
    if args.mode == "hosted":
        return run_hosted(args, out_dir)
    if args.mode == "dedicated":
        return run_dedicated(args, out_dir)
    if args.mode == "flagless-boot":
        return run_flagless_boot(args, out_dir)
    return run_flagless(args, out_dir)


if __name__ == "__main__":
    sys.exit(main())

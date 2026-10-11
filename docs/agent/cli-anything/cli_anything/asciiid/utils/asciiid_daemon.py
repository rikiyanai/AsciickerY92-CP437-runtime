"""asciiid MCP daemon — persists the asciiid subprocess across CLI invocations.

Lifecycle:
  1. `start_daemon()` double-forks into background, starts asciiid --mcp.
  2. Daemon listens on a Unix socket (~/.cli-anything-asciiid/mcp.sock).
  3. Each CLI invocation sends one command, reads raw stdout lines, disconnects.

Protocol (text, newline-delimited):
  Client → Daemon: <command>\n
  Daemon → Client: <raw asciiid stdout line>\n  (zero or more)
  Daemon → Client: __DAEMON_END__\n              (response complete)

Stop:
  Client sends: __DAEMON_STOP__\n
  Daemon sends QUIT to asciiid, cleans up, exits.

Concurrency:
  A single background thread reads ALL stdout from asciiid into a Queue.
  The request lock serialises commands — only one command runs at a time.
  This prevents readline threads from stealing lines across commands.
"""

import os
import queue
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

SOCK_PATH    = Path.home() / '.cli-anything-asciiid' / 'mcp.sock'
PID_PATH     = Path.home() / '.cli-anything-asciiid' / 'daemon.pid'
LOG_PATH     = Path.home() / '.cli-anything-asciiid' / 'daemon.log'
END_MARKER   = '__DAEMON_END__'
STOP_CMD     = '__DAEMON_STOP__'


# ── helpers ──────────────────────────────────────────────────────────

def _cleanup():
    for p in (SOCK_PATH, PID_PATH):
        try:
            if p.exists():
                p.unlink()
        except Exception:
            pass


# ── daemon main ──────────────────────────────────────────────────────

def _daemon_main(binary_path: str, project_root: str):
    """Runs in the daemonised child process."""
    SOCK_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Redirect stdio to log (keeps daemon output auditable)
    with open('/dev/null', 'r') as f:
        os.dup2(f.fileno(), 0)
    log = open(str(LOG_PATH), 'a', buffering=1)
    os.dup2(log.fileno(), 1)
    os.dup2(log.fileno(), 2)

    PID_PATH.write_text(str(os.getpid()))

    # Start asciiid --mcp
    try:
        proc = subprocess.Popen(
            [binary_path, '--mcp'],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=log,      # write to daemon log; avoids pipe-full deadlock
            cwd=project_root,
            text=True,
            bufsize=1,
        )
    except OSError as e:
        print(f'[daemon] failed to launch asciiid: {e}', flush=True)
        _cleanup()
        sys.exit(1)

    # ── stdout reader thread ──────────────────────────────────────────
    # Reads ALL asciiid stdout into a queue so that readline calls never
    # race across commands.
    stdout_q: queue.Queue[str | None] = queue.Queue()

    def _reader():
        for line in proc.stdout:
            stdout_q.put(line.rstrip('\n'))
        stdout_q.put(None)  # EOF sentinel

    reader_t = threading.Thread(target=_reader, daemon=True)
    reader_t.start()

    # ── wait for asciiid to be ready ──────────────────────────────────
    probe = '__DAEMON_READY_PROBE__'
    proc.stdin.write(f'ECHO {probe}\n')
    proc.stdin.flush()

    try:
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                err = proc.stderr.read(500) if proc.stderr else ''
                raise RuntimeError(f'asciiid exited during startup: {err}')
            try:
                line = stdout_q.get(timeout=1.0)
                if line is None:
                    raise RuntimeError('asciiid stdout closed during startup')
                if probe in line:
                    break
            except queue.Empty:
                pass
        else:
            raise RuntimeError('asciiid did not respond within 15s')
    except RuntimeError as e:
        print(f'[daemon] startup failed: {e}', flush=True)
        proc.kill()
        _cleanup()
        sys.exit(1)

    # Drain any remaining startup chatter
    drain_deadline = time.monotonic() + 3.0
    while time.monotonic() < drain_deadline:
        try:
            stdout_q.get(timeout=0.25)
        except queue.Empty:
            break

    print(f'[daemon] asciiid ready pid={proc.pid}', flush=True)

    # ── Unix socket ──────────────────────────────────────────────────
    if SOCK_PATH.exists():
        SOCK_PATH.unlink()
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(SOCK_PATH))
    srv.listen(8)
    srv.settimeout(1.0)

    lock = threading.Lock()
    counter = [0]
    stop_event = threading.Event()

    def handle(conn: socket.socket):
        try:
            # Read command (up to first newline)
            buf = b''
            conn.settimeout(5.0)
            while b'\n' not in buf:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                buf += chunk
            cmd = buf.split(b'\n', 1)[0].decode(errors='replace').strip()

            if not cmd:
                conn.sendall((END_MARKER + '\n').encode())
                return

            if cmd == STOP_CMD:
                stop_event.set()
                conn.sendall((END_MARKER + '\n').encode())
                return

            # Serialise: one command at a time
            with lock:
                counter[0] += 1
                sentinel = f'__DS{counter[0]}__'

                # Write command + sentinel probe to asciiid stdin
                proc.stdin.write(f'{cmd}\n')
                proc.stdin.write(f'ECHO {sentinel}\n')
                proc.stdin.flush()

                # Collect output lines until sentinel appears
                out_lines = []
                cmd_deadline = time.monotonic() + 60.0

                while time.monotonic() < cmd_deadline:
                    if proc.poll() is not None:
                        print(f'[daemon] asciiid exited mid-command', flush=True)
                        break
                    try:
                        line = stdout_q.get(timeout=1.0)
                    except queue.Empty:
                        continue
                    if line is None:
                        break  # stdout closed
                    if sentinel in line:
                        break
                    out_lines.append(line)

            # Send response back to client
            conn.settimeout(10.0)
            for ln in out_lines:
                conn.sendall((ln + '\n').encode())
            conn.sendall((END_MARKER + '\n').encode())

        except Exception as e:
            print(f'[daemon] handle error: {e}', flush=True)
            try:
                conn.sendall((END_MARKER + '\n').encode())
            except Exception:
                pass
        finally:
            conn.close()

    # ── accept loop ──────────────────────────────────────────────────
    while proc.poll() is None and not stop_event.is_set():
        try:
            conn, _ = srv.accept()
            threading.Thread(target=handle, args=(conn,), daemon=True).start()
        except socket.timeout:
            continue
        except OSError:
            break

    # ── shutdown ─────────────────────────────────────────────────────
    print('[daemon] shutting down', flush=True)
    try:
        if proc.poll() is None:
            proc.stdin.write('QUIT\n')
            proc.stdin.flush()
            proc.wait(timeout=3)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    srv.close()
    _cleanup()


# ── public API ───────────────────────────────────────────────────────

def start_daemon(binary_path: str, project_root: str, timeout: float = 20.0) -> int:
    """Fork the daemon; poll until the socket appears. Returns daemon PID."""
    pid = os.fork()
    if pid > 0:
        # Parent: reap intermediate child, then poll for socket
        os.waitpid(pid, 0)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if SOCK_PATH.exists() and daemon_alive():
                return int(PID_PATH.read_text().strip())
            time.sleep(0.15)
        raise RuntimeError(
            f'asciiid daemon did not start within {timeout}s — '
            f'check {LOG_PATH}'
        )

    # Intermediate child: detach from terminal and fork again
    os.setsid()
    pid2 = os.fork()
    if pid2 > 0:
        # Intermediate child exits → grandchild is orphaned (init adopts it)
        sys.exit(0)

    # Grandchild: this becomes the persistent daemon process
    _daemon_main(binary_path, project_root)
    sys.exit(0)


def daemon_alive() -> bool:
    """True if daemon process is running and socket file exists."""
    if not SOCK_PATH.exists() or not PID_PATH.exists():
        return False
    try:
        pid = int(PID_PATH.read_text().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, OSError):
        return False


def stop_daemon():
    """Ask the daemon to stop; force-kill if it does not."""
    if not daemon_alive():
        _cleanup()
        return
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(5.0)
        s.connect(str(SOCK_PATH))
        s.sendall((STOP_CMD + '\n').encode())
        s.recv(256)
        s.close()
    except Exception:
        pass
    # Give daemon a moment to clean up
    for _ in range(20):
        if not daemon_alive():
            return
        time.sleep(0.1)
    # Force-kill
    try:
        pid = int(PID_PATH.read_text().strip())
        os.kill(pid, 9)
    except (ValueError, OSError):
        pass
    _cleanup()


if __name__ == '__main__':
    if len(sys.argv) != 3:
        print('Usage: asciiid_daemon <binary_path> <project_root>')
        sys.exit(1)
    start_daemon(sys.argv[1], sys.argv[2])
    print(f'Daemon started. Socket: {SOCK_PATH}')

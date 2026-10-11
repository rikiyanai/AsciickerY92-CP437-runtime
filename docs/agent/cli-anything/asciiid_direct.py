#!/usr/bin/env python3
"""Direct asciiid subprocess session — no daemon, no sockets, no threads.

Spawns asciiid --mcp, holds stdin/stdout directly, uses blocking readline()
with ECHO sentinel for synchronization. Works for single-session workflows.

Usage:
    cd <project_root>
    python3 docs/agent/cli-anything/asciiid_direct.py
"""

import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
BINARY = PROJECT_ROOT / ".run" / "asciiid"


class AsciiidDirect:
    def __init__(self, binary=str(BINARY), cwd=str(PROJECT_ROOT)):
        self.proc = subprocess.Popen(
            [binary, "--mcp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=sys.stderr,  # show crashes immediately
            cwd=cwd,
            text=True,
            bufsize=1,
        )
        self._counter = 0

    def send(self, command: str) -> list[str]:
        """Send a command; block on readline() until ECHO sentinel returns."""
        self._counter += 1
        sentinel = f"__D{self._counter}__"
        self.proc.stdin.write(f"{command}\nECHO {sentinel}\n")
        self.proc.stdin.flush()
        lines = []
        while True:
            line = self.proc.stdout.readline()
            if not line:
                break  # EOF — process exited
            line = line.rstrip("\n")
            if sentinel in line:
                break
            lines.append(line)
        return lines

    def wait_ready(self, timeout: float = 15.0) -> bool:
        """Wait for asciiid to respond to a probe ECHO."""
        self._counter += 1
        sentinel = f"__READY{self._counter}__"
        self.proc.stdin.write(f"ECHO {sentinel}\n")
        self.proc.stdin.flush()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = self.proc.stdout.readline()
            if not line:
                return False
            if sentinel in line:
                return True
        return False

    def quit(self):
        try:
            self.proc.stdin.write("QUIT\n")
            self.proc.stdin.flush()
            self.proc.wait(timeout=3)
        except Exception:
            self.proc.kill()


def _print_lines(label: str, lines: list[str]):
    print(f"\n=== {label} ===")
    for l in lines:
        print(" ", l)


def main():
    print(f"Starting asciiid from {BINARY} ...")
    ed = AsciiidDirect()

    print("Waiting for asciiid to be ready...")
    if not ed.wait_ready(timeout=15):
        print("ERROR: asciiid did not respond within 15s")
        ed.proc.kill()
        sys.exit(1)
    print("asciiid ready.")

    # Load copy map
    map_path = "assets/a3d/copy_game_map_y8.a3d"
    print(f"\nLoading {map_path} (may take a while)...")
    lines = ed.send(f"LOAD_MAP {map_path}")
    _print_lines("LOAD_MAP", lines)

    rc = ed.proc.poll()
    if rc is not None:
        print(f"ERROR: asciiid exited with code {rc} after LOAD_MAP")
        sys.exit(1)

    # List all instances (find test markers mesh)
    print("\nQuerying instances...")
    lines = ed.send("LIST_INSTANCES")
    _print_lines("LIST_INSTANCES", lines)

    # List C++ markers (PLACE_MARKER objects)
    lines = ed.send("LIST_MARKERS")
    _print_lines("LIST_MARKERS", lines)

    # Get camera position
    lines = ed.send("GET_CAMERA")
    _print_lines("GET_CAMERA", lines)

    ed.quit()
    print("\nDone.")


if __name__ == "__main__":
    main()

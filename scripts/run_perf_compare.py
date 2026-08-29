#!/usr/bin/env python3
import argparse
import os
import signal
import subprocess
import sys
import time


def run_cmd(args, cwd):
    print("+ " + " ".join(args), flush=True)
    subprocess.run(args, cwd=cwd, check=True)


def run_game(bin_path, seconds, log_path):
    env = os.environ.copy()
    env["ASCIICKER_PROFILE"] = "1"
    env["ASCIICKER_PROFILE_LOG"] = log_path

    print("+ " + bin_path, flush=True)
    proc = subprocess.Popen([bin_path], env=env)
    try:
        time.sleep(seconds)
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


def main():
    parser = argparse.ArgumentParser(
        description="Build and run terminal + GL perf passes in order."
    )
    parser.add_argument("--seconds", type=int, default=10,
                        help="Seconds to run each pass.")
    parser.add_argument("--term-only", action="store_true",
                        help="Run only the terminal build.")
    parser.add_argument("--gl-only", action="store_true",
                        help="Run only the GL window build.")
    args = parser.parse_args()

    if args.term_only and args.gl_only:
        print("Choose only one of --term-only or --gl-only.", file=sys.stderr)
        return 2

    is_mac = sys.platform == "darwin"
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    term_make = "makefile_game_term_mac" if is_mac else "makefile_game_term"
    gl_make = "makefile_game_mac" if is_mac else "makefile_game"

    if not args.gl_only:
        run_cmd(["make", "-f", term_make], cwd=root)
        run_game(os.path.join(root, ".run", "game_term"),
                 args.seconds, os.path.join(root, "perf_term.log"))

    if not args.term_only:
        run_cmd(["make", "-f", gl_make], cwd=root)
        run_game(os.path.join(root, ".run", "game"),
                 args.seconds, os.path.join(root, "perf_gl.log"))

    print("Perf logs: perf_term.log, perf_gl.log", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

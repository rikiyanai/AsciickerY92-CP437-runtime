#!/usr/bin/env python3
"""Promote the verified candidate slot to the current release VM."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import textwrap
from pathlib import Path

from watchdog_deploy_constants import SYSTEMD_UNIT


SCRIPTS_DIR = Path(__file__).resolve().parent
SLOT_ADMIN = SCRIPTS_DIR / "watchdog_remote_slot_admin.py"

DEFAULT_SSH_KEY = Path.home() / ".ssh" / "google_compute_engine"
DEFAULT_SOURCE_HOST = "35.226.113.14"
DEFAULT_SOURCE_SLOT = "candidate"
DEFAULT_SOURCE_MACHINE_ROLE = "candidate"
DEFAULT_DEST_HOST = "34.11.68.149"
DEFAULT_DEST_SLOT = "release"
DEFAULT_DEST_MACHINE_ROLE = "release"
DEFAULT_CURRENT_HOSTNAME = "current.rikiworld.com"
CURRENT_STRIP_ENV_KEYS = (
    "ASCIICKER_DEBUG_TELEPORT",
    "ASCIICKER_DEBUG_SEED_ITEM",
    "ASCIICKER_DEBUG_SEED_AUTO_PICKUP",
    "ASCIICKER_DEBUG_NPC_START_HP",
    "ASCIICKER_DEBUG_SEED_INVENTORY",
    "ASCIICKER_DEBUG_START_HP",
    "ASCIICKER_DEBUG_DAMAGE",
)


def current_env_strip_fragment() -> str:
    deletes = " ".join(f"-e '/^{key}=.*/d'" for key in CURRENT_STRIP_ENV_KEYS)
    return (
        "env_file=/etc/default/asciicker-server; "
        'if [ -f "$env_file" ]; then '
        f"sed -i {deletes} \"$env_file\"; "
        "fi; "
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Promote candidate slot to current VM",
        epilog=textwrap.dedent(
            """\
            This is a mutating release operation. It copies the verified candidate
            slot to the current VM, restarts the current service, and verifies
            health/manifest endpoints. The launcher should print this command
            before execution; this script owns the remote promotion phases.
            """
        ),
    )
    p.add_argument("--ssh-key", default=str(DEFAULT_SSH_KEY))
    p.add_argument("--ssh-user", default="r")
    p.add_argument("--source-host", default=DEFAULT_SOURCE_HOST)
    p.add_argument("--source-slot", default=DEFAULT_SOURCE_SLOT)
    p.add_argument("--source-machine-role", default=DEFAULT_SOURCE_MACHINE_ROLE)
    p.add_argument("--dest-host", default=DEFAULT_DEST_HOST)
    p.add_argument("--dest-slot", default=DEFAULT_DEST_SLOT)
    p.add_argument("--dest-machine-role", default=DEFAULT_DEST_MACHINE_ROLE)
    p.add_argument("--current-hostname", default=DEFAULT_CURRENT_HOSTNAME)
    p.add_argument("--source-ref", default=None)
    p.add_argument("--build-id", default=None)
    p.add_argument("--current-link", default="current")
    p.add_argument(
        "--no-quiesce-source-service",
        action="store_true",
        help="Do not stop/restart the source asciicker-server during the cross-host tar copy.",
    )
    return p


def run_checked(cmd: list[str], *, label: str) -> subprocess.CompletedProcess:
    print(f"\n--- {label} ---")
    print("  " + " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout.strip())
    if result.returncode != 0:
        if result.stderr:
            print(result.stderr.strip(), file=sys.stderr)
        print()
        print("=" * 72, file=sys.stderr)
        print("PROMOTE FAILED", file=sys.stderr)
        print(f"  stage:       {label}", file=sys.stderr)
        print(f"  exit code:   {result.returncode}", file=sys.stderr)
        # Detect source_verify_failed (bundle hash / file-count mismatch) and give targeted advice
        if "source_verify_failed" in result.stdout or "source_verify_failed" in result.stderr:
            print("  failure:     source slot manifest mismatch (tree_manifest_sha256, build_inputs, etc.)", file=sys.stderr)
            print("  cause:       local compile state does not match the deployed candidate slot.", file=sys.stderr)
            print("  next action: redeploy the candidate slot with the current build, then rerun promote.", file=sys.stderr)
            print("    1. Build:   make (or the build target for your platform)", file=sys.stderr)
            print("    2. Deploy:  python3 scripts/launcher.py --action deploy-candidate-server", file=sys.stderr)
            print("    3. Promote: python3 scripts/launcher.py --action slot-promote", file=sys.stderr)
        else:
            print("  next action: inspect the command output above, repair the remote slot state, then rerun.", file=sys.stderr)
        print("=" * 72, file=sys.stderr)
        raise SystemExit(result.returncode)
    return result


def run_ssh_checked(
    *,
    ssh_key: str,
    ssh_user: str,
    host: str,
    remote_cmd: str,
    label: str,
) -> subprocess.CompletedProcess:
    return run_checked(
        [
            "ssh",
            "-i",
            ssh_key,
            "-o",
            "BatchMode=yes",
            f"{ssh_user}@{host}",
            remote_cmd,
        ],
        label=label,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not SLOT_ADMIN.exists():
        print(f"missing helper: {SLOT_ADMIN}", file=sys.stderr)
        print("next action: restore scripts/watchdog_remote_slot_admin.py, then rerun.", file=sys.stderr)
        return 2

    print("=" * 72)
    print("PROMOTE CANDIDATE TO CURRENT")
    print(f"  source:      {args.ssh_user}@{args.source_host} / {args.source_slot}")
    print(f"  destination: {args.ssh_user}@{args.dest_host} / {args.dest_slot}")
    print(f"  current URL: https://{args.current_hostname}")
    print(f"  mutates:     current VM files and {SYSTEMD_UNIT}")
    print("=" * 72)

    promote_cmd = [
        sys.executable,
        str(SLOT_ADMIN),
        "--ssh-key",
        args.ssh_key,
        "--ssh-user",
        args.ssh_user,
        "promote",
        "--source-host",
        args.source_host,
        "--source-slot",
        args.source_slot,
        "--source-machine-role",
        args.source_machine_role,
        "--dest-host",
        args.dest_host,
        "--dest-slot",
        args.dest_slot,
        "--dest-machine-role",
        args.dest_machine_role,
        "--current-link",
        args.current_link,
        "--switch-current",
    ]
    if args.source_ref:
        promote_cmd.extend(["--source-ref", args.source_ref])
    if args.build_id:
        promote_cmd.extend(["--build-id", args.build_id])

    source_quiesced = False
    try:
        if args.source_machine_role == "candidate" and not args.no_quiesce_source_service:
            run_ssh_checked(
                ssh_key=args.ssh_key,
                ssh_user=args.ssh_user,
                host=args.source_host,
                remote_cmd=f"sudo systemctl stop {SYSTEMD_UNIT} && systemctl is-active {SYSTEMD_UNIT} || true",
                label="Quiesce source candidate service",
            )
            source_quiesced = True

        promote = run_checked(promote_cmd, label="Promote slot")
        json.loads(promote.stdout)
    finally:
        if source_quiesced:
            run_ssh_checked(
                ssh_key=args.ssh_key,
                ssh_user=args.ssh_user,
                host=args.source_host,
                remote_cmd=f"sudo systemctl start {SYSTEMD_UNIT} && systemctl is-active {SYSTEMD_UNIT}",
                label="Restore source candidate service",
            )

    release_dir = f"/opt/asciicker/{args.dest_slot}"
    run_ssh_checked(
        ssh_key=args.ssh_key,
        ssh_user=args.ssh_user,
        host=args.dest_host,
        remote_cmd=(
            "set -e; "
            f"sudo chown -R r:asciicker {release_dir}; "
            f"sudo find {release_dir} -type d -exec chmod 2775 {{}} +; "
            f"sudo find {release_dir} -type f -exec chmod 664 {{}} +; "
            f"test ! -f {release_dir}/.run/server || sudo chmod 775 {release_dir}/.run/server; "
            f"sudo sh -lc '{current_env_strip_fragment()}'; "
            f"sudo systemctl restart {SYSTEMD_UNIT}; "
            "for i in $(seq 1 20); do "
            f"state=$(systemctl is-active {SYSTEMD_UNIT} || true); "
            "if [ \"$state\" = active ]; then echo active; exit 0; fi; "
            "sleep 1; "
            "done; "
            f"systemctl status {SYSTEMD_UNIT} --no-pager -l; "
            "exit 1"
        ),
        label="Normalize permissions and restart service",
    )

    verify_cmd = [
        "curl",
        "-fsS",
        f"https://{args.current_hostname}/health",
    ]
    run_checked(verify_cmd, label="Verify current health")

    manifest_cmd = [
        "curl",
        "-fsS",
        f"https://{args.current_hostname}/slot_manifest.json",
    ]
    run_checked(manifest_cmd, label="Fetch current manifest")
    print()
    print("=" * 72)
    print("PROMOTE SUMMARY")
    print("  result:      SUCCESS")
    print(f"  current URL: https://{args.current_hostname}")
    print("  next action: run current smoke from the launcher or with watchdog_run_canonical.py --mode current-smoke.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

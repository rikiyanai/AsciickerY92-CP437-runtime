#!/usr/bin/env python3
"""Canonical current SERVER deploy launcher.

This is the current-slot sibling of deploy_candidate_server.py. It deploys the
native server directly to /opt/asciicker/current/ on the release VM, builds on
the VM, restarts systemd, emits a release manifest, and verifies service health.

The web bundle is intentionally not handled here. deploy_candidate_web.py
already publishes .web/ to /opt/asciicker/current/.web as part of the existing
web lane.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import deploy_candidate_server as candidate_server  # noqa: E402

VPS_DEPLOY_ROOT = "/opt/asciicker/current"
VPS_SERVICE_BINARY = f"{VPS_DEPLOY_ROOT}/.run/server"
DEFAULT_SSH_TARGET = "r@34.11.68.149"
SLOT_NAME = "release"
MACHINE_ROLE = "release"
# DEPLOY CHECKLIST (CE code-review RQ-084 follow-up):
#   Before any deploy that touches nginx configs, run:
#     bash deploy/validate_nginx.sh
#   This validates syntax for proxy_intercept_errors + error_page rewrites.
CURRENT_STRIP_ENV_KEYS = (
    "ASCIICKER_DEBUG_TELEPORT",
    "ASCIICKER_DEBUG_SEED_ITEM",
    "ASCIICKER_DEBUG_SEED_AUTO_PICKUP",
    "ASCIICKER_DEBUG_NPC_START_HP",
    "ASCIICKER_DEBUG_SEED_INVENTORY",
    "ASCIICKER_DEBUG_START_HP",
    "ASCIICKER_DEBUG_DAMAGE",
)


def _current_env_strip_cmd() -> str:
    env_file = "/etc/default/asciicker-server"
    deletes = " ".join(f"-e '/^{key}=.*/d'" for key in CURRENT_STRIP_ENV_KEYS)
    return (
        "sudo sh -lc '"
        f"env_file={env_file}; "
        'if [ -f "$env_file" ]; then '
        f"sed -i {deletes} \"$env_file\"; "
        "fi'"
    )


def _patch_candidate_deploy_constants() -> None:
    candidate_server.VPS_DEPLOY_ROOT = VPS_DEPLOY_ROOT
    candidate_server.VPS_SERVICE_BINARY = VPS_SERVICE_BINARY


def phase_restart(ssh_target: str, identity_args: list[str]) -> bool:
    """Restart the live current service without candidate-only env arming."""
    print("\n--- Phase 5: Restart service ---")
    # Current must not inherit the candidate helper's mutating debug env owners.
    scrub = candidate_server._run_ssh(  # noqa: SLF001 - reuse the shared SSH transport only.
        ssh_target,
        _current_env_strip_cmd(),
        identity_args,
        "strip-current-debug-env",
        capture=True,
    )
    if scrub.returncode != 0:
        print("  FATAL: Failed to strip candidate-only env from current")
        return False

    r = candidate_server._run_ssh(  # noqa: SLF001 - reuse the shared SSH transport only.
        ssh_target,
        f"sudo systemctl restart {candidate_server.SYSTEMD_UNIT}",
        identity_args,
        "restart",
        capture=True,
    )
    if r.returncode != 0:
        print("  FATAL: Service restart failed")
        return False

    # Wait for service to stabilize.
    time.sleep(2)

    r = candidate_server._run_ssh(  # noqa: SLF001 - reuse the shared SSH transport only.
        ssh_target,
        f"systemctl is-active {candidate_server.SYSTEMD_UNIT}",
        identity_args,
        "check-active",
        capture=True,
    )
    state = r.stdout.strip() if r.stdout else "unknown"
    if state != "active":
        print(f"  FATAL: Service not active after restart (state: {state})")
        return False

    print(f"  Service state: {state}")
    print("  Restart complete")
    return True


def phase_emit_manifest(ssh_target: str, identity_args: list[str], source_ref: str) -> bool:
    """Emit the live release slot manifest after the current server build."""
    print("\n--- Phase 5b: Re-emit current release manifest ---")

    slot_admin = SCRIPTS_DIR / "watchdog_remote_slot_admin.py"
    if not slot_admin.exists():
        print(f"  FATAL: slot admin not found: {slot_admin}")
        return False

    ssh_key = None
    for i, arg in enumerate(identity_args):
        if arg == "-i" and i + 1 < len(identity_args):
            ssh_key = identity_args[i + 1]
            break

    if "@" in ssh_target:
        ssh_user, host = ssh_target.split("@", 1)
    else:
        ssh_user = "r"
        host = ssh_target

    cmd = [sys.executable, str(slot_admin)]
    if ssh_key:
        cmd.extend(["--ssh-key", ssh_key])
    cmd.extend([
        "--ssh-user", ssh_user,
        "emit",
        "--host", host,
        "--slot", SLOT_NAME,
        "--machine-role", MACHINE_ROLE,
        "--source-ref", source_ref,
    ])

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        print(f"  FATAL: slot-admin emit failed (exit {result.returncode})")
        if result.stdout:
            print(f"  stdout: {result.stdout.strip()}")
        if result.stderr:
            print(f"  stderr: {result.stderr.strip()}")
        return False

    try:
        response = json.loads(result.stdout)
        if response.get("ok"):
            print(f"  Manifest emitted: {response.get('manifest_path', '?')}")
            return True
        print(f"  FATAL: slot-admin returned ok=false: {response.get('error', '?')}")
        return False
    except json.JSONDecodeError:
        print(f"  FATAL: slot-admin output not JSON: {result.stdout.strip()}")
        return False


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Canonical current server deploy launcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--ssh-target", default=DEFAULT_SSH_TARGET,
                   help=f"SSH target for current deploy (default: {DEFAULT_SSH_TARGET})")
    p.add_argument("--source-dir", default=None,
                   help=f"Local source directory to sync (default: repo root = {REPO_ROOT})")
    p.add_argument("--source-ref", default=None,
                   help="Source ref for slot manifest (default: auto-detect from git)")
    p.add_argument("--skip-sync", action="store_true",
                   help="Skip rsync, only rebuild + restart (use when source is already synced)")
    p.add_argument("--skip-build", action="store_true",
                   help="Skip build, only restart service (use when binary is already built)")
    p.add_argument("--verify-only", action="store_true",
                   help="Only verify current VPS state, no deploy")
    p.add_argument("--dry-run", action="store_true",
                   help="Perform rsync dry-run, skip build/restart")
    return p


def main(argv: list[str] | None = None) -> int:
    _patch_candidate_deploy_constants()
    parser = build_parser()
    args = parser.parse_args(argv)

    identity_args = candidate_server._resolve_ssh_identity_args()
    source_dir = Path(args.source_dir) if args.source_dir else REPO_ROOT
    source_ref = args.source_ref or candidate_server._detect_source_ref(source_dir)

    ok, violations, _notes = candidate_server.check_multiplayer_canon(source_dir)
    if not ok:
        print("=" * 72)
        print("CURRENT SERVER DEPLOY BLOCKED — multiplayer canon-doc rule violated")
        for violation in violations:
            print(f"  {violation}")
        print()
        print("Repair the canon doc surface first, then retry.")
        print("=" * 72)
        return 2

    print("=" * 72)
    print("CURRENT SERVER DEPLOY LAUNCHER")
    print(f"  ssh-target:  {args.ssh_target}")
    print(f"  source-dir:  {source_dir}")
    print(f"  source-ref:  {source_ref}")
    print(f"  deploy-root: {VPS_DEPLOY_ROOT}")
    print(f"  slot:        {SLOT_NAME}")
    print(f"  service:     {candidate_server.SYSTEMD_UNIT}")
    print(f"  makefile:    {candidate_server.MAKEFILE}")
    print(f"  mode:        {'verify-only' if args.verify_only else 'dry-run' if args.dry_run else 'skip-sync' if args.skip_sync else 'full deploy'}")
    print("=" * 72)

    if not candidate_server.phase_verify_connectivity(args.ssh_target, identity_args):
        return 2

    if args.verify_only:
        return candidate_server.phase_verify_only(args.ssh_target, identity_args)

    pre_snapshot = candidate_server.phase_pre_deploy_snapshot(args.ssh_target, identity_args)

    if not args.skip_sync:
        if not source_dir.exists():
            print(f"FATAL: source directory does not exist: {source_dir}")
            return 2
        if not candidate_server.phase_rsync(args.ssh_target, identity_args, source_dir, dry_run=args.dry_run):
            return 1

    if args.dry_run:
        print("\nDRY RUN complete — no build or restart performed")
        return 0

    if not args.skip_build:
        if not candidate_server.phase_build(args.ssh_target, identity_args):
            return 1

    if not phase_restart(args.ssh_target, identity_args):
        return 1

    if not phase_emit_manifest(args.ssh_target, identity_args, source_ref):
        return 1

    post = candidate_server.phase_post_deploy_verify(args.ssh_target, identity_args, pre_snapshot)

    print()
    print("=" * 72)
    print("CURRENT DEPLOY SUMMARY")
    print(f"  result:         {'SUCCESS' if post['success'] else 'FAILED'}")
    print(f"  binary changed: {'yes' if post['binary_changed'] else 'no (identical source)'}")
    print(f"  service state:  {post['service_state']}")
    print()
    print("NEXT STEP — run current smoke:")
    print("  python3 scripts/watchdog_run_canonical.py --run-label passive --mode current-smoke --ssh-target r@34.11.68.149")
    print("=" * 72)

    return 0 if post["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

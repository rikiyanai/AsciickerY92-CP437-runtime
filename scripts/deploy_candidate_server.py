#!/usr/bin/env python3
"""Candidate SERVER deploy child executor (server-only, excludes .web/).

This script deploys the native server binary ONLY after the watchdog full-mode
front door has resolved the source contract. It explicitly excludes .web/ from
rsync — web artifacts are deployed separately by scripts/deploy_candidate_web.py.

Prevents: mixed-runtime drift, partial syncs, manual ad-hoc deploys,
undocumented build steps, and silent deploy failures.

Candidate host deployment is split into two independent launchers:
    - deploy_candidate_server.py  → native binary (rsync source + make + restart)
    - deploy_candidate_web.py     → web bundle (build + rsync .web/ + manifest emit)

Architecture:
    1. rsync local worktree → VPS candidate tree (excluding .web/)
    2. remote build: make -f makefile_server
    3. restart systemd service
    4. verify service health + binary freshness

The VPS has no git checkout — rsync is the only deploy path.
The server binary must be built on the VPS (Linux x86_64), not locally (macOS).

Usage:
    python3 scripts/watchdog_runner.py --target candidate --mode full

Direct mutating candidate server deploys are disabled. Use --verify-only for
inspection-only checks.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants — service unit name is owned by watchdog_deploy_constants.py
# ---------------------------------------------------------------------------
# DEPLOY CHECKLIST (CE code-review RQ-084 follow-up):
#   Before any deploy that touches nginx configs, run:
#     bash deploy/validate_nginx.sh
#   This validates syntax for proxy_intercept_errors + error_page rewrites.
# ---------------------------------------------------------------------------
VPS_DEPLOY_ROOT = "/opt/asciicker/candidate"
VPS_SERVICE_BINARY = f"{VPS_DEPLOY_ROOT}/.run/server"
VPS_ACTOR_VISUAL_PROFILE_DIR = f"{VPS_DEPLOY_ROOT}/assets/actor_visual_profiles/current"
MAKEFILE = "makefile_server"
SERVER_ENV_FILE = "/etc/default/asciicker-server"
CANDIDATE_RUNTIME_ENV_KEYS = (
    "ASCIICKER_PORT",
    "ASCIICKER_MAX_PLAYERS",
    "ASCIICKER_WATCHDOG_JOIN_ISOLATION",
    "ASCIICKER_AUTH_PUBLISH_INTERVAL",
)
MUTATING_DEBUG_ENV_KEYS = (
    "ASCIICKER_DEBUG_TELEPORT",
    "ASCIICKER_DEBUG_SEED_ITEM",
    "ASCIICKER_DEBUG_SEED_AUTO_PICKUP",
    "ASCIICKER_DEBUG_NPC_START_HP",
    "ASCIICKER_DEBUG_SEED_INVENTORY",
    "ASCIICKER_DEBUG_START_HP",
    "ASCIICKER_DEBUG_DAMAGE",
)

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))
from multiplayer_canon_guard import check_multiplayer_canon  # noqa: E402
from watchdog_deploy_constants import SYSTEMD_UNIT  # noqa: E402

# SSH identity — shared logic with watchdog_runner.py
DEFAULT_SSH_KEY = Path.home() / ".ssh" / "google_compute_engine"
SSH_CONTROL_PATH = "/tmp/asciicker-ssh-%C"
CANONICAL_FRONT_DOOR_COMMAND = (
    "python3 scripts/watchdog_runner.py --target candidate --mode full --yes "
    '--intent "candidate reset/deploy through watchdog full-mode front door"'
)

# rsync exclusions — prevent syncing local-only state and build artifacts
RSYNC_EXCLUDES = [
    ".git/",
    ".worktrees/",
    ".agents/",
    ".claude/",
    ".gsd.previous_project/",
    ".maintainer-hook-home/",
    "docs/agent/.mcp/",
    ".planning/",
    ".obsidian/",
    "obsidian vault/",
    "obsidian-vault/",
    # Maintainer evidence is not part of the candidate runtime surface.
    "artifacts/",
    "maintainer/",
    "backups/",
    "node_modules/",
    ".o_server/",
    ".d_server/",
    ".o_game/",
    ".d_game/",
    ".o_asciiid/",
    ".d_asciiid/",
    "output/",
    ".web/",
    ".run/",
    "scripts/launcher_helper_bot/index/",
    "__pycache__/",
    "*.pyc",
    ".venv/",
    "rexpaint_tmp/",
    ".DS_Store",
    "*.tar.gz",
]

REMOTE_DELETE_DIRS = [
    ".git",
    ".worktrees",
    ".agents",
    ".claude",
    ".gsd.previous_project",
    ".maintainer-hook-home",
    ".mcp",
    ".planning",
    ".obsidian",
    "obsidian vault",
    "obsidian-vault",
    "artifacts",
    "backups",
    "node_modules",
    ".o_server",
    ".d_server",
    ".o_game",
    ".d_game",
    ".o_asciiid",
    ".d_asciiid",
    "output",
    "scripts/launcher_helper_bot/index",
    "__pycache__",
    ".venv",
    "rexpaint_tmp",
]

REMOTE_DELETE_GLOBS = [
    "*.pyc",
    ".DS_Store",
    "*.tar.gz",
]


def candidate_runtime_env_text(join_isolation: bool = True) -> str:
    """Return the complete candidate runtime env file.

    FL-1150: candidate reset must rewrite one canonical env file, not mutate
    whatever stale keys happen to be present on the VPS.
    """
    iso_val = "1" if join_isolation else "0"
    lines = [
        "# Managed by scripts/deploy_candidate_server.py",
        "# Canonical candidate runtime env; rewritten on every server reset.",
        "ASCIICKER_PORT=8080",
        "ASCIICKER_MAX_PLAYERS=16",
        f"ASCIICKER_WATCHDOG_JOIN_ISOLATION={iso_val}",
        "ASCIICKER_AUTH_PUBLISH_INTERVAL=10",
        "",
    ]
    return "\n".join(lines)


def candidate_runtime_env_write_cmd(join_isolation: bool = True) -> str:
    env_text = candidate_runtime_env_text(join_isolation=join_isolation)
    quoted_env = shlex.quote(env_text)
    return (
        "tmp=$(mktemp /tmp/asciicker-server.env.XXXXXX) && "
        f"printf %s {quoted_env} > \"$tmp\" && "
        f"sudo install -o root -g root -m 0644 \"$tmp\" {SERVER_ENV_FILE} && "
        "rm -f \"$tmp\" && "
        f"echo CANONICAL_CANDIDATE_ENV_WRITTEN {SERVER_ENV_FILE} && "
        f"sudo sh -lc 'grep -E \"^ASCIICKER_\" {SERVER_ENV_FILE} || true'"
    )


def _resolve_ssh_identity_args() -> list[str]:
    """Return ['-i', path] if AK_SSH_KEY or default key exists, else []."""
    configured = os.environ.get("AK_SSH_KEY") or str(DEFAULT_SSH_KEY)
    if configured and Path(configured).exists():
        return ["-i", configured]
    return []


def _shared_ssh_control_args() -> list[str]:
    return [
        "-o", "ControlMaster=auto",
        "-o", "ControlPersist=60",
        "-o", f"ControlPath={SSH_CONTROL_PATH}",
    ]


def _ssh_cmd(ssh_target: str, remote_cmd: str, identity_args: list[str]) -> list[str]:
    """Build SSH command list."""
    return [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=10",
        *_shared_ssh_control_args(),
        *identity_args,
        ssh_target,
        remote_cmd,
    ]


def _run_ssh(ssh_target: str, remote_cmd: str, identity_args: list[str],
             label: str, capture: bool = False, timeout: int = 300) -> subprocess.CompletedProcess:
    """Run SSH command with consistent error handling."""
    cmd = _ssh_cmd(ssh_target, remote_cmd, identity_args)
    print(f"  [{label}] {remote_cmd}")
    result = subprocess.run(cmd, capture_output=capture, text=True, timeout=timeout)
    if result.returncode != 0 and capture:
        print(f"  [{label}] FAILED (exit {result.returncode})")
        if result.stderr:
            print(f"  [{label}] stderr: {result.stderr.strip()}")
    return result


def _detect_source_ref(source_dir: Path) -> str:
    """Auto-detect source ref from git HEAD."""
    try:
        r = subprocess.run(
            ["git", "-C", str(source_dir), "rev-parse", "--short=12", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return "unknown"


# ---------------------------------------------------------------------------
# Deploy phases
# ---------------------------------------------------------------------------

def phase_verify_connectivity(ssh_target: str, identity_args: list[str]) -> bool:
    """Gate 1: Verify SSH connectivity and basic VPS state."""
    print("\n--- Phase 1: Verify connectivity ---")
    r = _run_ssh(ssh_target,
                 f"echo SSH_OK && test -d {VPS_DEPLOY_ROOT} && echo DEPLOY_ROOT_OK "
                 f"&& which make g++ > /dev/null && echo TOOLCHAIN_OK "
                 f"&& test -f {VPS_DEPLOY_ROOT}/{MAKEFILE} && echo MAKEFILE_OK",
                 identity_args, "connectivity", capture=True)
    if r.returncode != 0:
        print(f"  FATAL: SSH connectivity check failed")
        return False
    output = r.stdout.strip()
    checks = {"SSH_OK", "DEPLOY_ROOT_OK", "TOOLCHAIN_OK", "MAKEFILE_OK"}
    found = set(output.split("\n"))
    missing = checks - found
    if missing:
        print(f"  FATAL: Missing checks: {missing}")
        return False
    print("  All connectivity checks PASSED")
    return True


def phase_pre_deploy_snapshot(ssh_target: str, identity_args: list[str]) -> dict:
    """Gate 2: Capture pre-deploy binary state for comparison."""
    print("\n--- Phase 2: Pre-deploy snapshot ---")
    r = _run_ssh(ssh_target,
                 f"md5sum {VPS_SERVICE_BINARY} 2>/dev/null || echo NO_BINARY; "
                 f"stat --format='%Y' {VPS_SERVICE_BINARY} 2>/dev/null || echo 0; "
                 f"systemctl is-active {SYSTEMD_UNIT} 2>/dev/null || echo inactive",
                 identity_args, "snapshot", capture=True)
    lines = r.stdout.strip().split("\n")
    snapshot = {
        "binary_md5": lines[0].split()[0] if len(lines) > 0 and lines[0] != "NO_BINARY" else None,
        "binary_mtime": int(lines[1]) if len(lines) > 1 and lines[1] != "0" else 0,
        "service_state": lines[2].strip() if len(lines) > 2 else "unknown",
    }
    print(f"  binary md5:    {snapshot['binary_md5'] or '(none)'}")
    print(f"  binary mtime:  {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(snapshot['binary_mtime']))}" if snapshot['binary_mtime'] else "  binary mtime:  (none)")
    print(f"  service state: {snapshot['service_state']}")
    return snapshot


def phase_rsync(ssh_target: str, identity_args: list[str], source_dir: Path,
                dry_run: bool = False) -> bool:
    """Phase 3: rsync local worktree to VPS candidate tree."""
    print("\n--- Phase 3: Sync source tree ---")

    # Build rsync command
    rsync_cmd = ["rsync", "-az", "--delete", "--stats"]

    # Add SSH identity
    ssh_parts = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=10",
        *_shared_ssh_control_args(),
    ]
    if identity_args:
        ssh_parts.extend(identity_args)
    rsync_cmd.extend(["-e", " ".join(ssh_parts)])

    # Exclusions
    for exc in RSYNC_EXCLUDES:
        rsync_cmd.extend(["--exclude", exc])

    if dry_run:
        rsync_cmd.append("--dry-run")

    # Source must end with / for rsync semantics (sync contents, not dir itself)
    rsync_cmd.append(f"{source_dir}/")
    rsync_cmd.append(f"{ssh_target}:{VPS_DEPLOY_ROOT}/")

    print(f"  source: {source_dir}/")
    print(f"  target: {ssh_target}:{VPS_DEPLOY_ROOT}/")
    if dry_run:
        print("  (DRY RUN — no actual transfer)")

    result = subprocess.run(rsync_cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        print(f"  FATAL: rsync failed (exit {result.returncode})")
        if result.stderr:
            print(f"  stderr: {result.stderr.strip()}")
        return False

    # Parse rsync stats
    for line in result.stdout.split("\n"):
        line = line.strip()
        if any(k in line for k in ["Number of files", "Total transferred", "sent", "received"]):
            print(f"  {line}")

    print("  Sync complete")
    return True


def phase_cleanup_remote_excludes(ssh_target: str, identity_args: list[str]) -> bool:
    """Delete local-only excluded owners from the remote slot before rsync."""
    print("\n--- Phase 2b: Delete excluded remote residue ---")

    quoted_dirs = " ".join(shlex.quote(f"{VPS_DEPLOY_ROOT}/{name}") for name in REMOTE_DELETE_DIRS)
    find_globs = " -o ".join(f"-name {shlex.quote(pattern)}" for pattern in REMOTE_DELETE_GLOBS)
    remote_cmd = (
        "sudo sh -lc "
        + shlex.quote(
            f"rm -rf {quoted_dirs}; "
            f"find {shlex.quote(VPS_DEPLOY_ROOT)} "
            f"\\( -type d \\( -name __pycache__ -o -name node_modules \\) "
            f"-o -type f \\( {find_globs} \\) \\) -print -exec rm -rf {{}} +"
        )
    )

    result = _run_ssh(
        ssh_target,
        remote_cmd,
        identity_args,
        "cleanup-excluded",
        capture=True,
        timeout=300,
    )
    if result.returncode != 0:
        print("  FATAL: failed to delete excluded remote residue")
        return False
    removed = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    print(f"  removed entries: {len(removed)}")
    return True


def phase_build(ssh_target: str, identity_args: list[str]) -> bool:
    """Phase 4: Build server binary on VPS."""
    print("\n--- Phase 4: Build server ---")

    build_cmd = (
        f"cd {VPS_DEPLOY_ROOT} && "
        f"sudo make -f {MAKEFILE} clean && "
        f"sudo make -f {MAKEFILE} -j1"
    )
    r = _run_ssh(ssh_target, build_cmd, identity_args, "build", capture=True, timeout=1200)
    if r.returncode != 0:
        if r.stdout:
            lines = r.stdout.strip().split("\n")
            for line in lines[-20:]:
                print(f"  {line}")
        if r.stderr:
            for line in r.stderr.strip().split("\n")[-10:]:
                print(f"  stderr: {line}")
        print(f"  FATAL: Build failed (exit {r.returncode})")
        return False

    # Show build output
    for line in r.stdout.strip().split("\n"):
        print(f"  {line}")

    # Verify binary was produced
    verify = _run_ssh(ssh_target,
                      f"test -f {VPS_SERVICE_BINARY} && echo BINARY_OK && "
                      f"stat --format='%s bytes, built %y' {VPS_SERVICE_BINARY}",
                      identity_args, "verify-binary", capture=True)
    if "BINARY_OK" not in (verify.stdout or ""):
        print(f"  FATAL: Binary not found after build")
        return False

    for line in verify.stdout.strip().split("\n"):
        if line != "BINARY_OK":
            print(f"  {line}")

    print("  Build complete")
    return True


def phase_fix_bundle_permissions(ssh_target: str, identity_args: list[str]) -> bool:
    """Ensure the runtime ActorVisualProfile subtree is readable by the service user after rsync."""
    print("\n--- Phase 3b: Normalize ActorVisualProfile permissions ---")
    remote_cmd = (
        "sudo sh -lc "
        + shlex.quote(
            f"if [ -d {shlex.quote(VPS_ACTOR_VISUAL_PROFILE_DIR)} ]; then "
            f"chmod 755 {shlex.quote(VPS_ACTOR_VISUAL_PROFILE_DIR)} && "
            f"find {shlex.quote(VPS_ACTOR_VISUAL_PROFILE_DIR)} -maxdepth 1 -type f -name '*.json' -exec chmod 644 {{}} + && "
            f"stat -c '%A %U:%G %n' {shlex.quote(VPS_ACTOR_VISUAL_PROFILE_DIR)} {shlex.quote(VPS_ACTOR_VISUAL_PROFILE_DIR)}/*.json; "
            "else "
            "echo MISSING_ACTOR_VISUAL_PROFILE_DIR; "
            "exit 1; "
            "fi"
        )
    )
    result = _run_ssh(
        ssh_target,
        remote_cmd,
        identity_args,
        "bundle-perms",
        capture=True,
        timeout=300,
    )
    if result.returncode != 0:
        print("  FATAL: failed to normalize appearance bundle permissions")
        if result.stdout:
            for line in result.stdout.strip().splitlines():
                print(f"  {line}")
        return False
    for line in result.stdout.strip().splitlines():
        print(f"  {line}")
    return True


def phase_restart(ssh_target: str, identity_args: list[str], join_isolation: bool = True) -> bool:
    """Phase 5: Restart the systemd service."""
    print("\n--- Phase 5: Restart service ---")
    env_write = _run_ssh(ssh_target,
                         candidate_runtime_env_write_cmd(join_isolation=join_isolation),
                         identity_args, "write-canonical-candidate-env", capture=True)
    if env_write.returncode != 0:
        print("  FATAL: failed to write canonical candidate env")
        if env_write.stdout:
            for line in env_write.stdout.strip().splitlines():
                print(f"  {line}")
        return False
    for line in env_write.stdout.strip().splitlines():
        print(f"  {line}")
    # FL-1148 disabled legacy candidate arming, preserved as reference only:
    # - ensure-debug-teleport: ASCIICKER_DEBUG_TELEPORT=1
    # - ensure-debug-seed-item: ASCIICKER_DEBUG_SEED_ITEM=1
    # - ensure-debug-seed-auto-pickup: ASCIICKER_DEBUG_SEED_AUTO_PICKUP=1
    # - ensure-debug-npc-start-hp: ASCIICKER_DEBUG_NPC_START_HP=30
    r = _run_ssh(ssh_target,
                 f"sudo systemctl restart {SYSTEMD_UNIT}",
                 identity_args, "restart", capture=True)
    if r.returncode != 0:
        print(f"  FATAL: Service restart failed")
        return False

    # Wait for service to stabilize
    time.sleep(2)

    # Check service state
    r = _run_ssh(ssh_target,
                 f"systemctl is-active {SYSTEMD_UNIT}",
                 identity_args, "check-active", capture=True)
    state = r.stdout.strip() if r.stdout else "unknown"
    if state != "active":
        print(f"  FATAL: Service not active after restart (state: {state})")
        return False

    print(f"  Service state: {state}")
    print("  Restart complete")
    return True


def phase_cleanup_stray_server_processes(ssh_target: str, identity_args: list[str]) -> bool:
    """Kill non-systemd candidate-slot server processes that can overwrite auth-state."""
    print("\n--- Phase 4b: Remove stray server writers ---")
    remote_cmd = """python3 - <<'PY'
import os
import signal
import subprocess
import sys
import time

UNIT = %r
SERVER_PATH = %r

def read_main_pid():
    proc = subprocess.run(
        ["systemctl", "show", UNIT, "--property=MainPID", "--value"],
        capture_output=True,
        text=True,
        check=False,
    )
    raw = (proc.stdout or "").strip() or "0"
    try:
        return int(raw)
    except ValueError:
        return 0

def server_processes():
    proc = subprocess.run(
        ["ps", "-eo", "pid=,args="],
        capture_output=True,
        text=True,
        check=False,
    )
    rows = []
    for line in (proc.stdout or "").splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        args = parts[1]
        if SERVER_PATH in args:
            rows.append((pid, args))
    return rows

main_pid = read_main_pid()
self_pid = os.getpid()
excluded_pids = {main_pid, self_pid, os.getppid()}
targets = [(pid, args) for pid, args in server_processes() if pid not in excluded_pids]
for pid, args in targets:
    print(f"STRAY_SERVER pid={pid} args={args}")
    killed = False
    kill_proc = subprocess.run(["sudo", "kill", str(pid)], capture_output=True, text=True, check=False)
    if kill_proc.returncode == 0:
        killed = True
    else:
        try:
            os.kill(pid, signal.SIGTERM)
            killed = True
        except OSError:
            pass
    if not killed:
        print(f"STRAY_SERVER_KILL_FAILED pid={pid}")
        sys.exit(1)

time.sleep(1)
remaining = [(pid, args) for pid, args in server_processes() if pid not in excluded_pids]
if remaining:
    for pid, args in remaining:
        print(f"STRAY_SERVER_FORCE_KILL pid={pid} args={args}")
        force_proc = subprocess.run(["sudo", "kill", "-9", str(pid)], capture_output=True, text=True, check=False)
        if force_proc.returncode != 0:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    time.sleep(1)
    remaining = [(pid, args) for pid, args in server_processes() if pid not in excluded_pids]
if remaining:
    for pid, args in remaining:
        print(f"STRAY_SERVER_REMAINING pid={pid} args={args}")
    sys.exit(1)

print(f"STRAY_SERVER_CLEANUP_OK main_pid={main_pid} removed={len(targets)}")
PY""" % (SYSTEMD_UNIT, VPS_SERVICE_BINARY)
    r = _run_ssh(
        ssh_target,
        remote_cmd,
        identity_args,
        "cleanup-stray-server-writers",
        capture=True,
        timeout=120,
    )
    if r.returncode != 0:
        print("  FATAL: failed to clear stray server writers")
        if r.stdout:
            for line in r.stdout.strip().splitlines():
                print(f"  {line}")
        return False
    for line in r.stdout.strip().splitlines():
        print(f"  {line}")
    return True


def phase_emit_manifest(ssh_target: str, identity_args: list[str], source_ref: str) -> bool:
    """Re-emit slot manifest so server.binary_sha256 is kept in sync."""
    print("\n--- Phase 5b: Re-emit slot manifest ---")

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
        "--slot", "candidate",
        "--machine-role", "candidate",
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


def classify_binary_refresh(pre_snapshot: dict, post: dict) -> dict[str, object]:
    """Classify whether deploy rebuilt the VPS binary and whether bytes changed."""
    binary_changed = post["binary_md5"] != pre_snapshot.get("binary_md5")
    mtime_advanced = post["binary_mtime"] > pre_snapshot.get("binary_mtime", 0)
    rebuild_observed = bool(binary_changed or mtime_advanced)
    if binary_changed:
        detail = "YES (rebuilt output differs from prior VPS binary)"
    elif rebuild_observed:
        detail = "NO (rebuilt output is byte-identical to prior VPS binary)"
    else:
        detail = "NO (no new binary timestamp observed)"
    return {
        "binary_changed": binary_changed,
        "mtime_advanced": mtime_advanced,
        "rebuild_observed": rebuild_observed,
        "detail": detail,
    }


def phase_post_deploy_verify(ssh_target: str, identity_args: list[str],
                             pre_snapshot: dict) -> dict:
    """Phase 6: Post-deploy verification."""
    print("\n--- Phase 6: Post-deploy verification ---")

    # Capture post-deploy state
    r = _run_ssh(ssh_target,
                 f"md5sum {VPS_SERVICE_BINARY}; "
                 f"stat --format='%Y' {VPS_SERVICE_BINARY}; "
                 f"systemctl is-active {SYSTEMD_UNIT}; "
                 f"journalctl -u {SYSTEMD_UNIT} -n 15 --no-pager",
                 identity_args, "post-verify", capture=True)

    lines = r.stdout.strip().split("\n")
    post = {
        "binary_md5": lines[0].split()[0] if len(lines) > 0 else None,
        "binary_mtime": int(lines[1]) if len(lines) > 1 else 0,
        "service_state": lines[2].strip() if len(lines) > 2 else "unknown",
        "journal_tail": "\n".join(lines[3:]) if len(lines) > 3 else "(empty)",
    }

    # Compare pre/post. A byte-identical hash does not prove "identical source":
    # the VPS may have rebuilt from current source and reproduced the same output.
    binary_refresh = classify_binary_refresh(pre_snapshot, post)
    binary_changed = bool(binary_refresh["binary_changed"])
    mtime_advanced = bool(binary_refresh["mtime_advanced"])
    rebuild_observed = bool(binary_refresh["rebuild_observed"])

    print(f"  binary md5:     {post['binary_md5']}")
    print(f"  rebuild observed: {'YES' if rebuild_observed else 'NO'}")
    print(f"  binary changed: {binary_refresh['detail']}")
    print(f"  mtime advanced: {'YES' if mtime_advanced else 'NO'}")
    print(f"  service state:  {post['service_state']}")
    print(f"\n  --- journal tail ---")
    print(f"  {post['journal_tail']}")
    print(f"  --- end journal ---")

    success = post["service_state"] == "active"
    if success:
        print("\n  POST-DEPLOY VERIFICATION: PASS")
    else:
        print("\n  POST-DEPLOY VERIFICATION: FAIL")

    post["binary_changed"] = binary_changed
    post["mtime_advanced"] = mtime_advanced
    post["rebuild_observed"] = rebuild_observed
    post["binary_change_detail"] = str(binary_refresh["detail"])
    post["success"] = success
    return post


def phase_verify_only(ssh_target: str, identity_args: list[str]) -> int:
    """Verify-only mode: check VPS state without deploying."""
    print("\n--- Verify-only mode ---")
    r = _run_ssh(ssh_target,
                 f"echo '=== binary ==='; "
                 f"md5sum {VPS_SERVICE_BINARY} 2>/dev/null || echo NO_BINARY; "
                 f"stat --format='size=%s built=%y' {VPS_SERVICE_BINARY} 2>/dev/null; "
                 f"echo '=== service ==='; "
                 f"systemctl is-active {SYSTEMD_UNIT}; "
                 f"echo '=== journal (last 20) ==='; "
                 f"journalctl -u {SYSTEMD_UNIT} -n 20 --no-pager; "
                 f"echo '=== makefile_server md5 ==='; "
                 f"md5sum {VPS_DEPLOY_ROOT}/{MAKEFILE}; "
                 f"echo '=== server_tick.cpp md5 ==='; "
                 f"md5sum {VPS_DEPLOY_ROOT}/server_tick.cpp 2>/dev/null || echo MISSING",
                 identity_args, "verify", capture=True)
    if r.stdout:
        for line in r.stdout.strip().split("\n"):
            print(f"  {line}")
    return 0 if r.returncode == 0 else 1


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Canonical candidate server deploy launcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--ssh-target", required=True,
                   help="SSH target for deploy (e.g., r@35.226.113.14)")
    p.add_argument("--source-dir", default=None,
                   help=f"Local source directory to sync (default: repo root = {REPO_ROOT})")
    p.add_argument("--source-ref", default=None,
                   help="Source ref for slot manifest (default: auto-detect from git)")
    p.add_argument("--skip-sync", action="store_true",
                   help="Skip rsync, only rebuild + restart (use when source is already synced)")
    p.add_argument("--skip-build", action="store_true",
                   help="Skip build, only restart service (use when binary is already built)")
    p.add_argument("--verify-only", action="store_true",
                   help="Only verify VPS state, no deploy")
    p.add_argument("--dry-run", action="store_true",
                   help="Perform rsync dry-run, skip build/restart")
    p.add_argument("--open-for-testing", action="store_true",
                   help="Disable join isolation so non-watchdog clients can connect; "
                        "implies --skip-sync --skip-build (restart-only, no deploy)")
    p.add_argument("--extract-chroma", action="store_true",
                   help="Extract chroma.tar.gz on the VPS after rsync "
                        "(for helper bot RAG queries on the VPS)")
    p.add_argument("--chroma-path", default="/opt/asciicker/candidate/scripts/launcher_helper_bot/index/chroma.tar.gz",
                   help="Remote path to chroma.tar.gz (default: candidate deploy root)")
    p.add_argument(
        "--internal-reset-executor",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return p


def mutating_public_invocation_blocker(args: argparse.Namespace) -> str | None:
    if args.verify_only:
        return None
    if args.internal_reset_executor and args.source_ref:
        return None
    if args.internal_reset_executor and not args.source_ref:
        return (
            "internal candidate server deploy requires --source-ref from the front-door "
            "restoreability contract; refusing to sync/build from an uncontracted live workspace"
        )
    return (
        "direct mutating candidate server deploy is disabled because it bypasses watchdog "
        "full-mode recovery, source contracts, and proof receipts"
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    public_blocker = mutating_public_invocation_blocker(args)
    if public_blocker:
        print("=" * 72)
        print("CANDIDATE SERVER DEPLOY BLOCKED — use hermetic watchdog front door")
        print(f"  reason: {public_blocker}")
        print()
        print("Run this instead:")
        print(f"  {CANONICAL_FRONT_DOOR_COMMAND}")
        print("=" * 72)
        return 2

    identity_args = _resolve_ssh_identity_args()
    source_dir = Path(args.source_dir) if args.source_dir else REPO_ROOT
    source_ref = args.source_ref or _detect_source_ref(source_dir)

    if getattr(args, "open_for_testing", False):
        args.skip_sync = True
        args.skip_build = True

    ok, violations, _notes = check_multiplayer_canon(source_dir)
    if not ok:
        print("=" * 72)
        print("CANDIDATE SERVER DEPLOY BLOCKED — multiplayer canon-doc rule violated")
        for violation in violations:
            print(f"  {violation}")
        print()
        print("Repair the canon doc surface first, then retry.")
        print("=" * 72)
        return 2

    print("=" * 72)
    print("CANDIDATE SERVER DEPLOY LAUNCHER")
    print(f"  ssh-target:  {args.ssh_target}")
    print(f"  source-dir:  {source_dir}")
    print(f"  source-ref:  {source_ref}")
    print(f"  deploy-root: {VPS_DEPLOY_ROOT}")
    print(f"  service:     {SYSTEMD_UNIT}")
    print(f"  makefile:    {MAKEFILE}")
    print(f"  mode:        {'verify-only' if args.verify_only else 'dry-run' if args.dry_run else 'skip-sync' if args.skip_sync else 'full deploy'}")
    print("=" * 72)

    # Gate 1: connectivity
    if not phase_verify_connectivity(args.ssh_target, identity_args):
        return 2

    # Verify-only mode
    if args.verify_only:
        return phase_verify_only(args.ssh_target, identity_args)

    # Gate 2: pre-deploy snapshot
    pre_snapshot = phase_pre_deploy_snapshot(args.ssh_target, identity_args)

    # Phase 3: sync
    if not args.skip_sync:
        if not phase_cleanup_remote_excludes(args.ssh_target, identity_args):
            return 1
        if not source_dir.exists():
            print(f"FATAL: source directory does not exist: {source_dir}")
            return 2
        if not phase_rsync(args.ssh_target, identity_args, source_dir, dry_run=args.dry_run):
            return 1

    if args.dry_run:
        print("\nDRY RUN complete — no build or restart performed")
        return 0

    if not phase_fix_bundle_permissions(args.ssh_target, identity_args):
        return 1

    # Phase 4: build
    if not args.skip_build:
        if not phase_build(args.ssh_target, identity_args):
            return 1

    if not phase_cleanup_stray_server_processes(args.ssh_target, identity_args):
        return 1

    # Phase 5: restart
    join_isolation = not getattr(args, "open_for_testing", False)
    if not phase_restart(args.ssh_target, identity_args, join_isolation=join_isolation):
        return 1

    restart_only = args.skip_sync and args.skip_build
    if restart_only:
        print("\n--- Phase 5b: Preserve existing slot manifest ---")
        print("  Restart-only mode: leaving slot_manifest.json unchanged because no sync/build/web deploy occurred")
    else:
        if not phase_emit_manifest(args.ssh_target, identity_args, source_ref):
            return 1

    # Phase 5c: extract chroma index on VPS (optional, for helper bot RAG)
    if args.extract_chroma:
        print("\n--- Phase 5c: Extract chroma index on VPS ---")
        chroma_remote_dir = f"{VPS_DEPLOY_ROOT}/scripts/launcher_helper_bot/index/chroma"
        chroma_archive = args.chroma_path
        extract_cmd = (
            f"mkdir -p {chroma_remote_dir} && "
            f"cd {VPS_DEPLOY_ROOT}/scripts/launcher_helper_bot/index && "
            f"tar xzf {chroma_archive} -C {VPS_DEPLOY_ROOT}/scripts/launcher_helper_bot/index/ "
            f"&& echo 'chroma extracted' || echo 'chroma extraction failed (archive not found)'"
        )
        r = _run_remote(args.ssh_target, identity_args, extract_cmd)
        if r.returncode == 0:
            print("  ✓ Chroma index extracted on VPS")
        else:
            print("  ⚠  Chroma extraction failed — see remote output")
            print(f"     Try: ssh {args.ssh_target} 'ls -la {chroma_archive}'")

    # Phase 6: post-deploy verification
    post = phase_post_deploy_verify(args.ssh_target, identity_args, pre_snapshot)

    # Summary
    print()
    print("=" * 72)
    print("DEPLOY SUMMARY")
    print(f"  result:         {'SUCCESS' if post['success'] else 'FAILED'}")
    print(f"  rebuild observed: {'yes' if post.get('rebuild_observed') else 'no'}")
    print(f"  binary changed: {post.get('binary_change_detail')}")
    print(f"  service state:  {post['service_state']}")
    print()
    print("NEXT STEP — run the proof launcher:")
    print(f"  python3 scripts/watchdog_runner.py --run-label passive --ssh-target {args.ssh_target} --mode full")
    print("=" * 72)

    return 0 if post["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

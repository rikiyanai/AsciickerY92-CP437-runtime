#!/usr/bin/env python3
"""Candidate web deploy child executor.

Deploys the local .web/ build to the VPS candidate web root after the watchdog
full-mode front door has resolved the source contract.
Does NOT restart the native server — use deploy_candidate_server.py for that.

The candidate host deployment is split into two independent launchers:
    - deploy_candidate_server.py  → native binary (rsync source + make + restart)
    - deploy_candidate_web.py     → web bundle (build + rsync .web/ + manifest emit)

Architecture:
    1. (Optional) Build .web/ locally via build-web.sh
    2. rsync web artifacts to the served web root and the candidate slot web root
    3. Re-emit slot manifest on VPS (via watchdog_remote_slot_admin.py)
    4. Verify served file hashes match local build

Usage:
    python3 scripts/watchdog_runner.py --target candidate --mode full

Direct mutating candidate web deploys are disabled. Use --verify-only for
inspection-only checks.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))
from multiplayer_canon_guard import check_multiplayer_canon  # noqa: E402

# Import shared verification (same directory)
from verify_candidate_web import (  # noqa: E402
    DEFAULT_BASE_URL,
    VPS_SERVED_WEB_ROOT,
    local_web_hashes,
    load_front_door_restoreability_contract,
    verify_local_build_freshness,
    verify_served_web,
)

# ---------------------------------------------------------------------------
# Constants — must match slot-admin / nginx / deploy contracts
# ---------------------------------------------------------------------------
VPS_DEPLOY_ROOT = "/opt/asciicker/candidate"
VPS_WEB_ROOT = f"{VPS_DEPLOY_ROOT}/.web"
VPS_SERVED_WEB_PUBLISH_ROOT = VPS_SERVED_WEB_ROOT
DEFAULT_SSH_KEY = Path.home() / ".ssh" / "google_compute_engine"
SSH_CONTROL_PATH = "/tmp/asciicker-ssh-%C"
CANONICAL_FRONT_DOOR_COMMAND = (
    "python3 scripts/watchdog_runner.py --target candidate --mode full --yes "
    '--intent "candidate reset/deploy through watchdog full-mode front door"'
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


def _ssh_run(ssh_target: str, remote_cmd: str, identity_args: list[str],
             label: str, capture: bool = True) -> subprocess.CompletedProcess:
    """Run SSH command with consistent error handling."""
    cmd = [
        "ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=10", *_shared_ssh_control_args(),
        *identity_args, ssh_target, remote_cmd,
    ]
    print(f"  [{label}] {remote_cmd}")
    result = subprocess.run(cmd, capture_output=capture, text=True, timeout=300)
    if result.returncode != 0 and capture:
        print(f"  [{label}] FAILED (exit {result.returncode})")
        if result.stderr:
            print(f"  [{label}] stderr: {result.stderr.strip()}")
    return result


# ---------------------------------------------------------------------------
# Deploy phases
# ---------------------------------------------------------------------------

def phase_build(source_dir: Path, *, source_ref: str | None = None) -> bool:
    """Phase 1: Build .web/ locally via build-web.sh.

    When the front door already resolved the committed source ref for this
    deploy, force the local manifest generator to stamp that same ref. Without
    this, build-web.sh can emit a fresh .web/slot_manifest.json from whatever
    git HEAD it detects locally while the deploy/verify path compares against a
    different explicit source_ref, producing a false freshness failure after a
    successful sync.
    """
    print("\n--- Phase 1: Build web locally ---")
    build_script = source_dir / "build-web.sh"
    if not build_script.exists():
        print(f"  FATAL: build-web.sh not found at {build_script}")
        return False

    print("  Running: bash build-web.sh")
    env = os.environ.copy()
    if source_ref:
        env["WATCHDOG_SOURCE_REF"] = source_ref
    result = subprocess.run(
        ["bash", str(build_script)],
        cwd=str(source_dir),
        timeout=600,
        env=env,
    )
    if result.returncode != 0:
        print(f"  FATAL: build-web.sh failed (exit {result.returncode})")
        return False

    # Verify core artifacts exist
    web_dir = source_dir / ".web"
    for f in ["index.html", "index.js", "index.wasm"]:
        if not (web_dir / f).exists():
            print(f"  FATAL: missing build output: .web/{f}")
            return False

    print("  Build complete")
    return True


def phase_hash_local(
    source_dir: Path,
    *,
    front_door_restoreability_contract: dict | None = None,
) -> tuple[dict | None, dict]:
    """Phase 2: Compute/display local hashes and verify local build freshness."""
    print("\n--- Phase 2: Local web hashes ---")
    web_dir = source_dir / ".web"
    hashes = local_web_hashes(web_dir)
    for key, val in hashes.items():
        short = val[:16] + "..." if val else "(missing)"
        print(f"  {key}: {short}")

    freshness = verify_local_build_freshness(
        web_dir,
        source_dir,
        front_door_restoreability_contract=front_door_restoreability_contract,
    )
    print(f"  Local build freshness: {freshness['verdict']}")
    for d in freshness["details"]:
        print(f"  {d}")
    if freshness["mismatches"]:
        print("  LOCAL BUILD FRESHNESS mismatches:")
        for m in freshness["mismatches"]:
            print(f"    {m['field']}: manifest={str(m.get('manifest', '?'))[:16]}... "
                  f"current={str(m.get('current', '?'))[:16]}...")
    if freshness["errors"]:
        print(f"  Errors: {freshness['errors']}")
    return (hashes if freshness["verdict"] == "PASS" else None), freshness


def phase_verify_connectivity(ssh_target: str, identity_args: list[str]) -> bool:
    """Phase 3a: Verify SSH connectivity and both VPS web roots exist."""
    print("\n--- Phase 3a: Verify connectivity ---")
    r = _ssh_run(
        ssh_target,
        f"echo SSH_OK && test -d {VPS_DEPLOY_ROOT} && echo DEPLOY_ROOT_OK "
        f"&& mkdir -p {VPS_SERVED_WEB_PUBLISH_ROOT} {VPS_WEB_ROOT} && echo WEB_ROOTS_OK",
        identity_args, "connectivity",
    )
    if r.returncode != 0:
        print("  FATAL: SSH connectivity check failed")
        return False
    output = r.stdout.strip()
    if "SSH_OK" not in output:
        print("  FATAL: SSH not reachable")
        return False
    print("  Connectivity: OK")
    return True


def phase_rsync(ssh_target: str, source_dir: Path, identity_args: list[str],
                dry_run: bool = False) -> bool:
    """Phase 3b: rsync .web/ to both served and slot web roots."""
    print("\n--- Phase 3b: Sync web artifacts ---")

    web_dir = source_dir / ".web"

    ssh_parts = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=10",
        *_shared_ssh_control_args(),
    ]
    if identity_args:
        ssh_parts.extend(identity_args)

    base_rsync_cmd = [
        "rsync", "-az", "--stats",
        "-e", " ".join(ssh_parts),
    ]

    if dry_run:
        base_rsync_cmd.append("--dry-run")

    targets = []
    seen_roots = set()
    for label, target_root in (
        ("served-web", VPS_SERVED_WEB_PUBLISH_ROOT),
        ("slot-web", VPS_WEB_ROOT),
    ):
        if target_root in seen_roots:
            continue
        seen_roots.add(target_root)
        targets.append((label, target_root))
    deploy_user = ssh_target.split("@", 1)[0] if "@" in ssh_target else None

    for label, target_root in targets:
        if not dry_run and target_root == VPS_WEB_ROOT and deploy_user:
            prep_owner = _ssh_run(
                ssh_target,
                f"sudo chown -R {deploy_user}:{deploy_user} {target_root}",
                identity_args,
                "prep-slot-web-owner",
            )
            if prep_owner.returncode != 0:
                print("  FATAL: failed to hand candidate/.web to deploy user before rsync")
                return False

        rsync_cmd = list(base_rsync_cmd)
        rsync_cmd.append(f"{web_dir}/")
        rsync_cmd.append(f"{ssh_target}:{target_root}/")

        print(f"  source: {web_dir}/")
        print(f"  target ({label}): {ssh_target}:{target_root}/")
        if dry_run:
            print("  (DRY RUN)")

        result = subprocess.run(rsync_cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            print(f"  FATAL: rsync failed for {label} (exit {result.returncode})")
            if result.stderr:
                print(f"  stderr: {result.stderr.strip()}")
            return False

        for line in result.stdout.split("\n"):
            line = line.strip()
            if any(k in line for k in ["Number of files", "Total transferred", "sent", "received"]):
                print(f"  {line}")

    if not dry_run:
        fix_perms = _ssh_run(
            ssh_target,
            f"sudo chown -R asciicker:asciicker {VPS_WEB_ROOT} "
            f"&& sudo chmod a+x {VPS_DEPLOY_ROOT} "
            f"&& sudo chmod u+rwx,go+rx {VPS_WEB_ROOT}",
            identity_args,
            "fix-candidate-web-perms",
        )
        if fix_perms.returncode != 0:
            print("  FATAL: failed to restore candidate/.web write permissions for runtime publisher")
            return False

    print("  Sync complete")
    return True


def phase_emit_manifest(ssh_target: str, identity_args: list[str],
                        source_ref: str) -> bool:
    """Phase 4: Re-emit slot manifest on VPS via watchdog_remote_slot_admin.py."""
    print("\n--- Phase 4: Re-emit slot manifest on VPS ---")

    slot_admin = SCRIPTS_DIR / "watchdog_remote_slot_admin.py"
    if not slot_admin.exists():
        print(f"  FATAL: slot admin not found: {slot_admin}")
        return False

    # Extract SSH key from identity_args
    ssh_key = None
    for i, arg in enumerate(identity_args):
        if arg == "-i" and i + 1 < len(identity_args):
            ssh_key = identity_args[i + 1]
            break

    # Extract host and user from ssh_target (user@host)
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

    print(f"  slot-admin emit --slot candidate --source-ref {source_ref}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)

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
            manifest_path = response.get("manifest_path", "?")
            print(f"  Manifest emitted: {manifest_path}")
            if VPS_WEB_ROOT != VPS_SERVED_WEB_PUBLISH_ROOT:
                publish_manifest = _ssh_run(
                    ssh_target,
                    f"cp {VPS_WEB_ROOT}/slot_manifest.json {VPS_SERVED_WEB_PUBLISH_ROOT}/slot_manifest.json",
                    identity_args,
                    "publish-manifest",
                )
                if publish_manifest.returncode != 0:
                    print("  FATAL: failed to publish slot_manifest.json to served web root")
                    return False
            return True
        else:
            print(f"  FATAL: slot-admin returned ok=false: {response.get('error', '?')}")
            return False
    except json.JSONDecodeError:
        print(f"  FATAL: slot-admin output not JSON: {result.stdout.strip()}")
        return False


def phase_verify(ssh_target: str, base_url: str, local_hashes: dict,
                 identity_args: list[str], source_dir: Path,
                 *, front_door_restoreability_contract: dict | None = None) -> dict:
    """Phase 5: Verify served files match local build and manifest."""
    print("\n--- Phase 5: Post-deploy verification ---")

    result = verify_served_web(
        ssh_target=ssh_target,
        base_url=base_url,
        expected_local_hashes=local_hashes,
        local_web_dir=source_dir / ".web",
        repo_root=source_dir,
        identity_args=identity_args,
        front_door_restoreability_contract=front_door_restoreability_contract,
    )

    filtered_local_manifest_vs_live = [
        m for m in (result.get("local_manifest_vs_live") or [])
        if not str(m.get("field", "")).startswith("runtime.")
    ]
    server_only_manifest_mismatch = bool(result.get("manifest_vs_served")) and all(
        m.get("file") == "server" for m in result["manifest_vs_served"]
    )
    if (
        server_only_manifest_mismatch
        and not result.get("local_vs_served")
        and not filtered_local_manifest_vs_live
        and not result.get("errors")
    ):
        result = dict(result)
        result["manifest_vs_served"] = []
        result["local_manifest_vs_live"] = []
        result["verdict"] = "PASS"
        result.setdefault("details", []).append(
            "web-only verify: ignored remote-tree provenance drift fields (server/runtime) that are not updated by .web rsync"
        )

    print(f"  Verdict: {result['verdict']}")
    for d in result["details"]:
        print(f"  {d}")

    if result["manifest_vs_served"]:
        print("  MANIFEST vs SERVED mismatches:")
        for m in result["manifest_vs_served"]:
            print(f"    {m['file']}: manifest={str(m.get('manifest', '?'))[:16]}... "
                  f"served={str(m.get('served', '?'))[:16]}...")

    if result["local_vs_served"]:
        print("  LOCAL vs SERVED mismatches:")
        for m in result["local_vs_served"]:
            print(f"    {m['file']}: local={str(m.get('local', '?'))[:16]}... "
                  f"served={str(m.get('served', '?'))[:16]}...")

    if result["errors"]:
        print(f"  Errors: {result['errors']}")
    if result.get("local_freshness", {}).get("mismatches"):
        print("  LOCAL FRESHNESS mismatches:")
        for m in result["local_freshness"]["mismatches"]:
            print(f"    {m['field']}: manifest={str(m.get('manifest', '?'))[:16]}... "
                  f"current={str(m.get('current', '?'))[:16]}...")

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Canonical candidate web deploy launcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--ssh-target", required=True,
                   help="SSH target (e.g., r@35.226.113.14)")
    p.add_argument("--source-dir", default=None,
                   help=f"Local source directory (default: repo root = {REPO_ROOT})")
    p.add_argument("--base-url", default=DEFAULT_BASE_URL,
                   help=f"Public base URL (default: {DEFAULT_BASE_URL})")
    p.add_argument("--source-ref", default=None,
                   help="Source ref for manifest (default: auto-detect from git)")
    p.add_argument("--skip-build", action="store_true",
                   help="Skip local build (assumes .web/ already built)")
    p.add_argument("--verify-only", action="store_true",
                   help="Only verify served state, no deploy")
    p.add_argument("--dry-run", action="store_true",
                   help="rsync dry-run, no manifest emit or verification")
    p.add_argument(
        "--front-door-restoreability-contract",
        default=None,
        help="JSON contract emitted by watchdog_runner.py full-mode front door",
    )
    p.add_argument(
        "--internal-reset-executor",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    p.add_argument("--extract-chroma", action="store_true",
                   help="Extract chroma.tar.gz on the VPS after deploy "
                        "(for helper bot RAG queries on the VPS)")
    p.add_argument("--chroma-path",
                   default="/opt/asciicker/candidate/scripts/launcher_helper_bot/index/chroma.tar.gz",
                   help="Remote path to chroma.tar.gz")
    return p


def mutating_public_invocation_blocker(args: argparse.Namespace) -> str | None:
    if args.verify_only:
        return None
    if args.internal_reset_executor and args.front_door_restoreability_contract:
        return None
    if args.internal_reset_executor and not args.front_door_restoreability_contract:
        return (
            "internal candidate web deploy requires --front-door-restoreability-contract; "
            "refusing to build/sync from an uncontracted live workspace"
        )
    return (
        "direct mutating candidate web deploy is disabled because it bypasses watchdog "
        "full-mode recovery, source contracts, and proof receipts"
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    public_blocker = mutating_public_invocation_blocker(args)
    if public_blocker:
        print("=" * 72)
        print("CANDIDATE WEB DEPLOY BLOCKED — use hermetic watchdog front door")
        print(f"  reason: {public_blocker}")
        print()
        print("Run this instead:")
        print(f"  {CANONICAL_FRONT_DOOR_COMMAND}")
        print("=" * 72)
        return 2

    identity_args = _resolve_ssh_identity_args()
    source_dir = Path(args.source_dir) if args.source_dir else REPO_ROOT
    source_ref = args.source_ref or _detect_source_ref(source_dir)
    try:
        front_door_restoreability_contract = load_front_door_restoreability_contract(
            args.front_door_restoreability_contract
        )
    except ValueError as exc:
        print(f"FATAL: {exc}")
        return 2

    ok, violations, _notes = check_multiplayer_canon(source_dir)
    if not ok:
        print("=" * 72)
        print("CANDIDATE WEB DEPLOY BLOCKED — multiplayer canon-doc rule violated")
        for violation in violations:
            print(f"  {violation}")
        print()
        print("Repair the canon doc surface first, then retry.")
        print("=" * 72)
        return 2

    print("=" * 72)
    print("CANDIDATE WEB DEPLOY LAUNCHER")
    print(f"  ssh-target:  {args.ssh_target}")
    print(f"  source-dir:  {source_dir}")
    print(f"  base-url:    {args.base_url}")
    print(f"  source-ref:  {source_ref}")
    print(f"  deploy-root: {VPS_DEPLOY_ROOT}")
    print(f"  web-root:    {VPS_WEB_ROOT}")
    print(f"  mode:        {'verify-only' if args.verify_only else 'dry-run' if args.dry_run else 'full deploy'}")
    print("=" * 72)

    # ── Verify-only mode ───────────────────────────────────────────────
    if args.verify_only:
        local_hashes = None
        web_dir = source_dir / ".web"
        if web_dir.exists():
            local_hashes = local_web_hashes(web_dir)
        result = phase_verify(
            args.ssh_target,
            args.base_url,
            local_hashes or {},
            identity_args,
            source_dir,
            front_door_restoreability_contract=front_door_restoreability_contract,
        )
        return 0 if result["verdict"] == "PASS" else 1

    # ── Phase 1: Build ─────────────────────────────────────────────────
    if not args.skip_build:
        if not phase_build(source_dir, source_ref=source_ref):
            return 1
    else:
        web_dir = source_dir / ".web"
        if not web_dir.exists():
            print("FATAL: .web/ directory not found (--skip-build requires pre-built .web/)")
            return 2

    # ── Phase 2: Hash local ────────────────────────────────────────────
    local_hashes, freshness = phase_hash_local(
        source_dir,
        front_door_restoreability_contract=front_door_restoreability_contract,
    )
    if local_hashes is None:
        if any(
            str(error).startswith("front_door_recovery_required:")
            for error in freshness.get("errors", [])
        ):
            print("\nFATAL: front-door restoreability contract drifted during candidate web deploy")
        else:
            print("\nFATAL: local .web bundle is stale relative to current build inputs")
        print("Required reset:")
        print("  ./build-web.sh")
        print(f"  python3 scripts/deploy_candidate_web.py --ssh-target {args.ssh_target}")
        return 2

    # ── Phase 3a: Connectivity ─────────────────────────────────────────
    if not phase_verify_connectivity(args.ssh_target, identity_args):
        return 2

    # ── Phase 3b: Rsync ────────────────────────────────────────────────
    if not phase_rsync(args.ssh_target, source_dir, identity_args, dry_run=args.dry_run):
        return 1

    if args.dry_run:
        print("\nDRY RUN complete — no manifest emit or verification performed")
        return 0

    # ── Phase 4: Emit manifest ─────────────────────────────────────────
    if not phase_emit_manifest(args.ssh_target, identity_args, source_ref):
        return 1

    # ── Phase 5: Verify ────────────────────────────────────────────────
    result = phase_verify(
        args.ssh_target,
        args.base_url,
        local_hashes,
        identity_args,
        source_dir,
        front_door_restoreability_contract=front_door_restoreability_contract,
    )

    # ── Summary ────────────────────────────────────────────────────────
    print()
    print("=" * 72)
    print("WEB DEPLOY SUMMARY")
    print(f"  result:       {result['verdict']}")
    print(f"  source-ref:   {source_ref}")
    if result["manifest"]:
        print(f"  manifest-ref: {result['manifest'].get('source_ref', '?')}")
    print()
    if result["verdict"] == "PASS":
        print("NEXT STEP — run the proof launcher:")
        print(f"  python3 scripts/watchdog_runner.py --run-label passive --ssh-target {args.ssh_target}")
    else:
        print("VERIFICATION FAILED — do not run proof until resolved")
    print("=" * 72)

    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

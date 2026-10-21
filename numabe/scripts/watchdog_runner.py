#!/usr/bin/env python3
"""Canonical watchdog front door with an isolated local two-tab phase.

The local phase is the mandatory prerequisite for the later candidate VPS
phase. It freezes one committed source identity, builds server and web output
from an exact archive snapshot, invokes the headed two-tab collector, then leaves
gameplay interpretation to post-run analyzers.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from watchdog.analyze_receipt import build_summary
from watchdog.phases.build_local import build_local
from watchdog.phases.collect_runtime import collect_local_runtime
from watchdog.phases.recorder_health import inspect_recorder_health
from watchdog.phases.source_identity import (
    describe_source,
    prepare_source_snapshot,
    remove_prepared_source,
)
import deploy_candidate_server as candidate_server_deploy


# FL-4512 watchdog routing contract (Classic default, explicit Current).
# The local two-tab path builds server output (makefile_server) plus the web
# WASM bundle (build-web.sh, which compiles current-runtime glyph sources)
# and drives them with the external two-tab collector on the sandbox proof
# map. That stack exercises the FL4512/Current runtime, never the Classic
# CP437 native binary — so --mode local requires the explicit Current
# product flag and fails closed under the Classic default. Field names mirror
# scripts/sbu_e2e_run.py --runtime-product.
RUNTIME_PRODUCT_CLASSIC = "classic_cp437"
RUNTIME_PRODUCT_CURRENT = "fl4512_unicode"
RUNTIME_PRODUCT_CHOICES = (RUNTIME_PRODUCT_CLASSIC, RUNTIME_PRODUCT_CURRENT)
RUNTIME_RENDERER = {
    RUNTIME_PRODUCT_CLASSIC: "classic_cp437",
    RUNTIME_PRODUCT_CURRENT: "fl4512_unicode",
}
LOCAL_RUNTIME_BUILD = "server(makefile_server)+web(build-web.sh)"
LOCAL_PROOF_MAP = "assets/a3d/sandbox_20x20.a3d"
LOCAL_COLLECTOR = "scripts/proofs/proof_candidate_two_tab_smoothing.js"


def _controller_timeout_seconds(raw: str) -> int:
    """Parse --controller-timeout-seconds, rejecting non-positive values.

    FL-4600: long ASan diagnostic runs need a caller-chosen wall-clock ceiling
    for the controller/analyzer subprocess instead of a fixed 180s. This is the
    argparse ``type`` for that flag so an invalid value fails CLOSED at parse
    time (argparse raises SystemExit) rather than silently falling back to a
    default. A timeout of 0 or a negative number is meaningless for
    ``subprocess.run(timeout=...)`` (0 would expire immediately, negatives raise
    at call time), so both are rejected here.

    raw: the raw command-line string for the flag.
    Returns the validated positive integer number of seconds.
    """
    try:
        value = int(raw)
    except ValueError:
        # Non-integer text (e.g. "abc", "1.5") is rejected fail-closed.
        raise argparse.ArgumentTypeError(
            f"--controller-timeout-seconds must be a positive integer, got {raw!r}"
        )
    if value <= 0:
        # Zero or negative seconds is not a usable subprocess timeout.
        raise argparse.ArgumentTypeError(
            f"--controller-timeout-seconds must be a positive integer, got {value}"
        )
    return value


def _movement_pulse_ms(raw: str) -> int:
    """Parse the physical WASD pulse duration, rejecting invalid values."""
    try:
        value = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"--movement-pulse-ms must be a positive integer, got {raw!r}"
        )
    if value <= 0:
        raise argparse.ArgumentTypeError(
            f"--movement-pulse-ms must be a positive integer, got {value}"
        )
    return value


def _validate_local_timing(args: argparse.Namespace) -> None:
    """Reject a controller ceiling shorter than the historical run floor."""
    required_seconds = max(180, args.drive_ms // 1000 + 150)
    if args.controller_timeout_seconds < required_seconds:
        raise RuntimeError(
            "--controller-timeout-seconds must be at least "
            f"{required_seconds} for --drive-ms {args.drive_ms}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="watchdog_runner.py",
        description="Canonical watchdog proof run front door",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--target", choices=["candidate", "current"])
    parser.add_argument(
        "--runtime-product",
        choices=list(RUNTIME_PRODUCT_CHOICES),
        default=RUNTIME_PRODUCT_CLASSIC,
        help=(
            "runtime product under proof (default: classic_cp437). "
            "The local two-tab path serves the FL4512 web runtime, so "
            "--mode local requires explicit --runtime-product fl4512_unicode."
        ),
    )
    parser.add_argument(
        "--mode",
        default="watchdog-only",
        choices=[
            "full",
            "watchdog-only",
            "local",
            "candidate-reset",
            "current-smoke",
        ],
    )
    parser.add_argument("--run-label", default="passive")
    parser.add_argument("--ssh-target")
    parser.add_argument("--base-url")
    parser.add_argument("--ws-server")
    parser.add_argument("--controller-mode", choices=["manual", "recipe", "off"])
    parser.add_argument("--controller-recipe")
    # FL-4600: sole owner of the controller/analyzer subprocess wall-clock
    # timeout. Default 300 covers the complete physical composite choreography
    # plus the configured drive window; long ASan diagnostic runs raise it
    # further (e.g. --controller-timeout-seconds 600). Validated positive
    # fail-closed by _controller_timeout_seconds; this CLI value is the ONLY
    # timeout source (no hard-coded fallback remains downstream).
    parser.add_argument(
        "--controller-timeout-seconds",
        type=_controller_timeout_seconds,
        default=300,
    )
    parser.add_argument(
        "--movement-pulse-ms",
        type=_movement_pulse_ms,
        default=35,
        help="physical WASD key-hold duration used by local recipe navigation",
    )
    parser.add_argument("--repeat-exact-run", metavar="RUN_ID")
    parser.add_argument("--repeat-exact-last", action="store_true")
    parser.add_argument("--followup-repeat-with-derived-recipe", action="store_true")
    parser.add_argument("--commits")
    parser.add_argument(
        "--intent-diff-corpus", choices=["gameplay", "watchdog", "launcher", "all"]
    )
    parser.add_argument(
        "--intent-diff-mode", choices=["latest_relevant", "between_runs"]
    )
    parser.add_argument("--commit-all-and-reset", action="store_true")
    parser.add_argument("--proof-profile")
    parser.add_argument("--auto-fl", action="store_true")
    parser.add_argument("--observation")
    parser.add_argument("--intent")
    parser.add_argument("--intent-introduced-by", action="append", default=[])
    parser.add_argument("--intent-fix-attempt", action="append", default=[])
    parser.add_argument("--intent-baseline-run", action="append", default=[])
    parser.add_argument("--intent-required-fields", action="append", default=[])
    parser.add_argument("--auto", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--drive-ms", type=int, default=12000)
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=REPO_ROOT / "artifacts/maintainer/watchdog_runs",
    )
    return parser


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def _run_id(label: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "run"
    return f"{stamp}-{slug}"


def _analyze_actor_visual_parity(artifact_dir: Path, timeout_seconds: int) -> dict:
    # FL-4600: timeout_seconds is a REQUIRED parameter with no default so the
    # CLI flag --controller-timeout-seconds is the single source of truth for
    # this subprocess's wall-clock ceiling. The former hard-coded timeout=180
    # was deleted; keeping a default here would re-introduce a second, silent
    # timeout owner.
    command = [
        sys.executable,
        str(REPO_ROOT / "scripts/analyze_runs.py"),
        "actor-visual-parity",
        "--artifact-path",
        str(artifact_dir),
        "--json",
    ]
    result = subprocess.run(
        command,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "actor-visual-parity analyzer failed: "
            + (result.stderr or result.stdout).strip()
        )
    return json.loads(result.stdout)


def _print_plan(args: argparse.Namespace) -> None:
    source = describe_source(REPO_ROOT, args.commits)
    print("watchdog_runner.py [dry-run]")
    print(f"  mode:              {args.mode}")
    print(f"  target:            {args.target or '(not set)'}")
    print(f"  run-label:         {args.run_label}")
    print(f"  runtime-product:   {args.runtime_product}")
    print(f"  source-ref:        {source.source_ref}")
    print("  source-policy:     exact-commit archive allowlist")
    print(f"  controller-mode:   {args.controller_mode or 'off'}")
    if args.mode == "local":
        print(f"  controller-timeout:{args.controller_timeout_seconds}s")
        print(f"  movement-pulse:    {args.movement_pulse_ms}ms")
        print("  phases:            source_identity, build_local, two_tab_collect, analyzer")
        print(f"  runtime-build:     {LOCAL_RUNTIME_BUILD}")
        print(f"  proof-map:         {LOCAL_PROOF_MAP}")
        print(f"  collector:         {LOCAL_COLLECTOR}")
        print(f"  controller-recipe: {args.controller_recipe or '(none)'}")
        if args.runtime_product == RUNTIME_PRODUCT_CLASSIC:
            print("  local preflight:   REFUSE under the Classic default; re-run with --runtime-product fl4512_unicode")
        else:
            print("  local preflight:   PROCEED as explicit Current (FL4512 web runtime; not the Classic binary)")
        print("  proof-class:       local prerequisite; operator signoff still required")
    elif args.mode == "candidate-reset":
        print("  phases:            capture_slot, canonical_env, restart_existing, ws_probe")
        print("  proof-class:       recovery only; no deployment and no closure claim")
    else:
        print("  status:            candidate deployment phase is not implemented")


def _run_local(args: argparse.Namespace) -> int:
    if args.controller_mode not in (None, "recipe"):
        raise RuntimeError("local automated two-tab evidence requires --controller-mode recipe")
    if args.runtime_product != RUNTIME_PRODUCT_CURRENT:
        raise RuntimeError(
            "local two-tab evidence serves the FL4512 web runtime "
            f"({LOCAL_RUNTIME_BUILD} on {LOCAL_PROOF_MAP}); refusing the "
            f"Classic default product {args.runtime_product!r} — re-run with "
            "--runtime-product fl4512_unicode for explicit Current"
        )
    _validate_local_timing(args)
    run_id = _run_id(args.run_label)
    artifact_dir = args.artifact_root.expanduser().resolve() / run_id
    artifact_dir.mkdir(parents=True, exist_ok=False)
    started_at = datetime.now(timezone.utc).isoformat()
    source = None
    try:
        source = prepare_source_snapshot(REPO_ROOT, args.commits)
        _write_json(artifact_dir / "source_identity.json", source.to_json())
        build_receipt = build_local(source, artifact_dir)
        _write_json(artifact_dir / "build_receipt.json", build_receipt)
        runtime_receipt = collect_local_runtime(
            source=source,
            invocation_repo=REPO_ROOT,
            artifact_dir=artifact_dir,
            run_id=run_id,
            drive_ms=args.drive_ms,
            controller_timeout_seconds=args.controller_timeout_seconds,
            movement_pulse_ms=args.movement_pulse_ms,
            runtime_product=args.runtime_product,
        )
        _write_json(artifact_dir / "runtime_receipt.json", runtime_receipt)
        recorder_health = inspect_recorder_health(artifact_dir)
        _write_json(artifact_dir / "recorder_health.json", recorder_health)
        parity = _analyze_actor_visual_parity(
            artifact_dir, args.controller_timeout_seconds
        )
        _write_json(artifact_dir / "actor_visual_parity.json", parity)
        summary = build_summary(
            run_id=run_id,
            run_label=args.run_label,
            artifact_dir=artifact_dir,
            source_ref=source.source_ref,
            started_at=started_at,
            recorder_health=recorder_health,
            actor_visual_parity=parity,
            runtime_product=args.runtime_product,
            runtime_map=LOCAL_PROOF_MAP,
            controller_mode=args.controller_mode,
            controller_recipe=args.controller_recipe,
        )
        # FL-4600: record the selected controller timeout in the run receipt
        # (summary.json) that watchdog_runner owns, so the receipt documents the
        # exact wall-clock ceiling used by the controller and analyzer subprocesses.
        summary["controller_timeout_seconds"] = args.controller_timeout_seconds
        summary["movement_pulse_ms"] = args.movement_pulse_ms
        _write_json(artifact_dir / "summary.json", summary)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0 if summary["proof_classification"] == "PROVEN_PASS" else 2
    except BaseException as exc:
        _write_json(
            artifact_dir / "launch_failure.json",
            {
                "run_id": run_id,
                "started_at_utc": started_at,
                "failed_at_utc": datetime.now(timezone.utc).isoformat(),
                "error": str(exc),
                "runtime_product": args.runtime_product,
                "runtime_map": LOCAL_PROOF_MAP,
                "controller_timeout_seconds": args.controller_timeout_seconds,
                "movement_pulse_ms": args.movement_pulse_ms,
                "closure_eligible": False,
            },
        )
        raise
    finally:
        if source is not None:
            remove_prepared_source(source)


def _fetch_json(url: str) -> dict:
    with urlopen(url, timeout=20) as response:
        return json.load(response)


def _candidate_manifest_url(args: argparse.Namespace) -> str:
    base_url = (args.base_url or "https://candidate-asciicker.rikiworld.com").rstrip("/")
    return f"{base_url}/slot_manifest.json"


def _candidate_ws_probe_target(args: argparse.Namespace) -> str:
    if args.ws_server:
        value = args.ws_server.strip()
        if value.startswith(("ws://", "wss://")):
            return value
        return f"wss://{value.rstrip('/')}/ws/y8/"
    return "wss://candidate-asciicker.rikiworld.com/ws/y8/"


def _runtime_identity(manifest: dict) -> dict:
    web = manifest.get("web") or {}
    server = manifest.get("server") or {}
    return {
        "source_ref": manifest.get("source_ref"),
        "git_dirty": manifest.get("git_dirty"),
        "server_sha256": server.get("sha256"),
        "index_html_sha256": web.get("index_html_sha256"),
        "index_js_sha256": web.get("index_js_sha256"),
        "index_wasm_sha256": web.get("index_wasm_sha256"),
        "index_data_sha256": web.get("index_data_sha256"),
        "generated_table_sha256": web.get("generated_table_sha256"),
        "server_reachability_identity_sha256": web.get(
            "server_reachability_identity_sha256"
        ),
    }


def _candidate_server_snapshot(ssh_target: str) -> dict:
    """Capture actual candidate process identity without trusting web metadata."""
    identity_args = candidate_server_deploy._resolve_ssh_identity_args()
    remote_command = (
        "set -u; "
        f"printf 'binary_sha256='; sha256sum {candidate_server_deploy.VPS_SERVICE_BINARY} "
        "2>/dev/null | awk '{print $1}'; "
        f"printf 'binary_size='; stat --format='%s' {candidate_server_deploy.VPS_SERVICE_BINARY} "
        "2>/dev/null || printf '0\\n'; "
        f"printf 'binary_mtime='; stat --format='%Y' {candidate_server_deploy.VPS_SERVICE_BINARY} "
        "2>/dev/null || printf '0\\n'; "
        f"printf 'service_state='; systemctl is-active {candidate_server_deploy.SYSTEMD_UNIT} "
        "2>/dev/null || true; "
        f"printf 'main_pid='; systemctl show {candidate_server_deploy.SYSTEMD_UNIT} "
        "--property=MainPID --value 2>/dev/null || printf '0\\n'; "
        f"printf 'service_started_at='; systemctl show {candidate_server_deploy.SYSTEMD_UNIT} "
        "--property=ExecMainStartTimestamp --value 2>/dev/null || true; "
        f"printf 'env_sha256='; sha256sum {candidate_server_deploy.SERVER_ENV_FILE} "
        "2>/dev/null | awk '{print $1}'"
    )
    result = candidate_server_deploy._run_ssh(
        ssh_target,
        remote_command,
        identity_args,
        "candidate-runtime-snapshot",
        capture=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "candidate remote runtime snapshot failed: "
            + (result.stderr or result.stdout).strip()
        )
    values = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key.strip()] = value.strip()
    required = {
        "binary_sha256",
        "binary_size",
        "binary_mtime",
        "service_state",
        "main_pid",
        "service_started_at",
        "env_sha256",
    }
    missing = sorted(required - values.keys())
    if missing:
        raise RuntimeError(
            "candidate remote runtime snapshot omitted fields: " + ", ".join(missing)
        )
    try:
        values["binary_size"] = int(values["binary_size"])
        values["binary_mtime"] = int(values["binary_mtime"])
        values["main_pid"] = int(values["main_pid"])
    except ValueError as exc:
        raise RuntimeError("candidate remote runtime snapshot has invalid integers") from exc
    return values


def _run_candidate_reset(args: argparse.Namespace) -> int:
    """Restart unchanged candidate bytes, then prove process and socket recovery."""
    if args.target != "candidate":
        raise RuntimeError("candidate-reset requires --target candidate")
    if not args.ssh_target:
        raise RuntimeError("candidate-reset requires --ssh-target")

    run_id = _run_id(args.run_label)
    artifact_dir = args.artifact_root.expanduser().resolve() / run_id
    artifact_dir.mkdir(parents=True, exist_ok=False)
    started_at = datetime.now(timezone.utc).isoformat()
    manifest_url = _candidate_manifest_url(args)
    try:
        before_manifest = _fetch_json(manifest_url)
        before_manifest_identity = _runtime_identity(before_manifest)
        if not before_manifest_identity["source_ref"]:
            raise RuntimeError("candidate slot manifest has no source_ref")
        _write_json(artifact_dir / "candidate_manifest_before.json", before_manifest)
        before_server = _candidate_server_snapshot(args.ssh_target)
        _write_json(artifact_dir / "candidate_server_before.json", before_server)
        if not before_server["binary_sha256"] or before_server["binary_size"] <= 0:
            raise RuntimeError("candidate remote server binary is missing")

        reset_command = [
            sys.executable,
            str(SCRIPTS_DIR / "deploy_candidate_server.py"),
            "--ssh-target",
            args.ssh_target,
            "--source-ref",
            str(before_manifest_identity["source_ref"]),
            "--skip-sync",
            "--skip-build",
            "--open-for-testing",
            "--internal-reset-executor",
        ]
        reset_result = subprocess.run(
            reset_command,
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            timeout=300,
        )
        (artifact_dir / "candidate_reset.log").write_text(
            reset_result.stdout + reset_result.stderr,
            encoding="utf-8",
        )
        if reset_result.returncode != 0:
            raise RuntimeError(
                f"candidate server reset failed ({reset_result.returncode})"
            )

        after_manifest = _fetch_json(manifest_url)
        after_manifest_identity = _runtime_identity(after_manifest)
        _write_json(artifact_dir / "candidate_manifest_after.json", after_manifest)
        if after_manifest_identity != before_manifest_identity:
            raise RuntimeError(
                "candidate reset changed the historical slot manifest; refusing recovery claim"
            )
        after_server = _candidate_server_snapshot(args.ssh_target)
        _write_json(artifact_dir / "candidate_server_after.json", after_server)
        if (
            after_server["binary_sha256"] != before_server["binary_sha256"]
            or after_server["binary_size"] != before_server["binary_size"]
        ):
            raise RuntimeError(
                "candidate reset changed actual remote server bytes; refusing recovery claim"
            )
        if after_server["service_state"] != "active":
            raise RuntimeError("candidate service is not active after reset")
        if after_server["main_pid"] <= 0:
            raise RuntimeError("candidate service has no live MainPID after reset")
        if after_server["main_pid"] == before_server["main_pid"]:
            raise RuntimeError("candidate service MainPID did not change during reset")
        expected_env_sha256 = hashlib.sha256(
            candidate_server_deploy.candidate_runtime_env_text(
                join_isolation=False
            ).encode("utf-8")
        ).hexdigest()
        if after_server["env_sha256"] != expected_env_sha256:
            raise RuntimeError(
                "candidate runtime environment does not match the canonical reset template"
            )

        probe_command = [
            sys.executable,
            str(SCRIPTS_DIR / "probe_ws_lag_rtt.py"),
            "--target",
            _candidate_ws_probe_target(args),
            "--samples",
            "5",
            "--interval-ms",
            "250",
            "--timeout-s",
            "8",
            "--json",
        ]
        probe_result = subprocess.run(
            probe_command,
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            timeout=60,
        )
        (artifact_dir / "candidate_ws_probe.log").write_text(
            probe_result.stdout + probe_result.stderr,
            encoding="utf-8",
        )
        if probe_result.returncode != 0:
            raise RuntimeError(
                f"candidate WebSocket probe failed after reset ({probe_result.returncode})"
            )
        ws_probe = json.loads(probe_result.stdout)
        _write_json(artifact_dir / "candidate_ws_probe.json", ws_probe)

        summary = {
            "run_id": run_id,
            "run_label": args.run_label,
            "mode": "candidate-reset",
            "target": "candidate",
            "started_at_utc": started_at,
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "verdict": "pass",
            "proof_classification": "RECOVERY_PASS",
            "closure_eligible": False,
            "operator_signoff_required": True,
            "artifact_identity_unchanged": True,
            "actual_remote_server_identity_unchanged": True,
            "manifest_identity_unchanged": True,
            "manifest_is_runtime_proof": False,
            "manifest_identity": after_manifest_identity,
            "candidate_server_before": before_server,
            "candidate_server_after": after_server,
            "canonical_env_sha256": expected_env_sha256,
            "ws_probe": ws_probe,
            "law16_disclaimer": (
                "A reset pass proves process recovery against unchanged deployed bytes. "
                "It does not prove gameplay, visual parity, block placement, or closure."
            ),
        }
        _write_json(artifact_dir / "summary.json", summary)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    except BaseException as exc:
        _write_json(
            artifact_dir / "launch_failure.json",
            {
                "run_id": run_id,
                "started_at_utc": started_at,
                "failed_at_utc": datetime.now(timezone.utc).isoformat(),
                "error": str(exc),
                "closure_eligible": False,
            },
        )
        raise


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.dry_run:
        _print_plan(args)
        return 0
    if args.mode not in ("local", "candidate-reset"):
        print(
            "ERROR: canonical candidate deployment is not implemented; "
            "run --mode local to satisfy the mandatory exact-source prerequisite.",
            file=sys.stderr,
        )
        return 3
    try:
        if args.mode == "candidate-reset":
            return _run_candidate_reset(args)
        return _run_local(args)
    except BaseException as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

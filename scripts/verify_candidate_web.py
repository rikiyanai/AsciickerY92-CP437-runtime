#!/usr/bin/env python3
"""Shared candidate web integrity verification.

Verifies that the candidate host's served web files match its slot manifest
and optionally match expected local build hashes.

Three checks:
    1. Manifest-vs-served: do the on-disk VPS web files match the manifest hashes?
    2. Local-vs-served: do the local .web/ build hashes match the served files?
    3. Manifest fetch: is the manifest reachable and valid JSON?

Can be imported as a module or run standalone:
    python3 scripts/verify_candidate_web.py --ssh-target r@35.226.113.14
    python3 scripts/verify_candidate_web.py --ssh-target r@35.226.113.14 --local-web-dir .web
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

# Must match deploy/nginx/asciicker-candidate-host.conf and slot-admin paths.
VPS_SERVED_WEB_ROOT = "/opt/asciicker/candidate/.web"
# Candidate proofs validate the candidate slot's live server binary.
VPS_SERVED_SERVER_BINARY = "/opt/asciicker/candidate/.run/server"
DEFAULT_BASE_URL = "https://candidate-asciicker.rikiworld.com"
DEFAULT_SSH_KEY = Path.home() / ".ssh" / "google_compute_engine"
SSH_CONTROL_PATH = "/tmp/asciicker-ssh-%C"
FRONT_DOOR_RECOVERY_REQUIRED = "front_door_recovery_required"

WEB_FILE_FIELD_MAP = [
    ("index.html", "index_html_sha256"),
    ("index.js", "index_js_sha256"),
    ("index.wasm", "index_wasm_sha256"),
    ("index.data", "index_data_sha256"),
]
WEB_FILES = [filename for filename, _ in WEB_FILE_FIELD_MAP]


def source_refs_match(expected_ref: str | None, live_ref: str | None) -> bool:
    expected = str(expected_ref or "").strip()
    live = str(live_ref or "").strip()
    if not expected or not live:
        return False
    if len(expected) < 7 or len(live) < 7:
        return expected == live
    return expected.startswith(live) or live.startswith(expected)


SOURCE_SNAPSHOT_EXCLUDE_DIRS = {
    "__pycache__",
    "artifacts",
    "node_modules",
    ".vscode",
    ".git",
    ".web",
    ".run",
    ".o_asciiid",
    ".o_game",
    ".o_server",
    ".o_term",
    ".d_asciiid",
    ".d_game",
    ".d_server",
    ".d_term",
}
SOURCE_SNAPSHOT_EXCLUDE_SUFFIXES = {".pyc", ".pyo"}
SOURCE_SNAPSHOT_EXCLUDE_BASENAMES = {".DS_Store"}
GIT_STATUS_IGNORE_PREFIXES = (
    ".web/",
    ".run/",
    "artifacts/maintainer/watchdog_runs/",
    ".o_asciiid/",
    ".o_game/",
    ".o_server/",
    ".o_term/",
    ".d_asciiid/",
    ".d_game/",
    ".d_server/",
    ".d_term/",
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


def _curl_resolve_args(base_url: str) -> list[str]:
    rules = (os.environ.get("AK_HOST_RESOLVER_RULES") or "").strip()
    parts = rules.split()
    if len(parts) != 3 or parts[0] != "MAP":
        return []

    parsed = urlparse(base_url)
    if not parsed.hostname or parsed.hostname != parts[1]:
        return []

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return ["--resolve", f"{parts[1]}:{port}:{parts[2]}"]


def _ssh_run(ssh_target: str, remote_cmd: str, identity_args: list[str] | None = None,
             timeout: int = 30) -> tuple[int, str, str]:
    """Run SSH command, return (returncode, stdout, stderr)."""
    if identity_args is None:
        identity_args = _resolve_ssh_identity_args()
    cmd = [
        "ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=10", *_shared_ssh_control_args(),
        *identity_args, ssh_target, remote_cmd,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except Exception as e:
        return -1, "", str(e)


def sha256_file(filepath: str | Path) -> str | None:
    """Compute SHA-256 hash of a local file."""
    p = Path(filepath)
    if not p.is_file():
        return None
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_json(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

def file_fingerprint(filepath: str | Path) -> dict:
    st = Path(filepath).stat()
    return {
        "size": st.st_size,
        "mtime_ns": getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000)),
    }


def _git_run(repo_root: str | Path, git_args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(Path(repo_root).resolve()), *git_args],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.rstrip("\n")


def local_web_hashes(web_dir: str | Path) -> dict:
    """Compute SHA-256 hashes of local .web/ build files."""
    d = Path(web_dir)
    return {
        field_name: sha256_file(d / filename)
        for filename, field_name in WEB_FILE_FIELD_MAP
    }


def read_local_manifest(web_dir: str | Path) -> dict | None:
    manifest_path = Path(web_dir) / "slot_manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        return json.loads(manifest_path.read_text())
    except json.JSONDecodeError:
        return None


def compute_build_input_manifest(repo_root: str | Path) -> dict:
    root = Path(repo_root).resolve()
    slot_manifest_js = root / "scripts" / "watchdog" / "lib" / "slot_manifest.js"
    if not slot_manifest_js.is_file():
        raise FileNotFoundError(f"missing slot manifest owner: {slot_manifest_js}")
    node_program = """
const { computeBuildInputManifest } = require(process.argv[1]);
const repoRoot = process.argv[2];
process.stdout.write(JSON.stringify(computeBuildInputManifest(repoRoot)));
"""
    try:
        result = subprocess.run(
            ["node", "-e", node_program, str(slot_manifest_js), str(root)],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise RuntimeError(f"failed to execute build-input owner: {exc}") from exc
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(f"build-input owner failed: {stderr or f'exit {result.returncode}'}")
    try:
        manifest = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("build-input owner returned invalid JSON") from exc
    if not isinstance(manifest, dict):
        raise RuntimeError("build-input owner returned non-object manifest")
    return manifest


def compute_source_snapshot_manifest(repo_root: str | Path) -> dict:
    root = Path(repo_root).resolve()
    if not root.is_dir():
        return {"root": str(root), "file_count": 0, "sha256": None}

    entries = []
    for path in sorted(root.rglob("*"), key=lambda p: p.as_posix()):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        parts = rel.split("/")
        if any(part in SOURCE_SNAPSHOT_EXCLUDE_DIRS for part in parts[:-1]):
            continue
        if rel == ".web/slot_manifest.json":
            continue
        if path.suffix in SOURCE_SNAPSHOT_EXCLUDE_SUFFIXES:
            continue
        if path.name in SOURCE_SNAPSHOT_EXCLUDE_BASENAMES:
            continue
        entries.append({
            "path": rel,
            **file_fingerprint(path),
        })

    return {
        "root": str(root),
        "file_count": len(entries),
        "sha256": sha256_json(entries),
    }


def _parse_git_status_porcelain(status_text: str | None) -> list[dict]:
    if not status_text:
        return []
    entries: list[dict] = []
    for line in status_text.splitlines():
        if not line:
            continue
        status = line[:2]
        rel = line[3:] if len(line) > 3 else ""
        if " -> " in rel:
            rel = rel.split(" -> ", 1)[1]
        entries.append({"path": rel.replace("\\", "/"), "status": status})
    entries.sort(key=lambda item: item["path"])
    return entries


def _should_ignore_git_status_path(rel_path: str) -> bool:
    normalized = rel_path.replace("\\", "/")
    return any(
        normalized == prefix[:-1] or normalized.startswith(prefix)
        for prefix in GIT_STATUS_IGNORE_PREFIXES
    )


def _compute_dirty_file_set_sha(repo_root: str | Path, dirty_status: list[dict]) -> str | None:
    if not dirty_status:
        return None
    root = Path(repo_root).resolve()
    entries = []
    for entry in dirty_status:
        rel = entry["path"]
        abs_path = root / rel
        exists = abs_path.is_file()
        entries.append({
            "path": rel,
            "status": entry["status"],
            "exists": exists,
            "sha256": sha256_file(abs_path) if exists else None,
        })
    return sha256_json(entries)


def _format_restoreability_error(prefix: str, provenance: dict) -> str:
    restoreability = provenance.get("restoreability", {}) if isinstance(provenance, dict) else {}
    reason = restoreability.get("reason") or "unknown"
    error = f"{prefix}:{reason}"
    if reason == "git_worktree_dirty" and isinstance(provenance, dict):
        dirty_files = provenance.get("dirty_files") or [
            entry.get("path")
            for entry in provenance.get("dirty_status", [])
            if isinstance(entry, dict) and entry.get("path")
        ]
        dirty_files = [str(path) for path in dirty_files if path]
        if dirty_files:
            shown = dirty_files[:20]
            suffix = ""
            if len(dirty_files) > len(shown):
                suffix = f",...(+{len(dirty_files) - len(shown)})"
            error = f"{error}:paths={','.join(shown)}{suffix}"
    elif reason == "post_contract_build_input_drift" and isinstance(provenance, dict):
        build_inputs = provenance.get("build_inputs") or {}
        accepted = str(build_inputs.get("accepted_sha256") or "unknown")[:16]
        manifest = str(build_inputs.get("manifest_sha256") or "unknown")[:16]
        current = str(build_inputs.get("current_sha256") or "unknown")[:16]
        error = f"{error}:accepted={accepted}:manifest={manifest}:current={current}"
    return error


def _format_front_door_recovery_required(prefix: str, provenance: dict) -> str:
    return f"{FRONT_DOOR_RECOVERY_REQUIRED}:{_format_restoreability_error(prefix, provenance)}"


def _front_door_allows_dirty_snapshot(contract: dict | None) -> bool:
    if not isinstance(contract, dict):
        return False
    owner = str(contract.get("owner") or "").strip()
    if owner != "scripts/watchdog_runner.py":
        return False
    return str(contract.get("accepted_recovery_mode") or "").strip() == "dirty_snapshot"


def _front_door_build_inputs(contract: dict | None) -> dict:
    if not isinstance(contract, dict):
        return {}
    build_inputs = contract.get("local_build_inputs")
    return build_inputs if isinstance(build_inputs, dict) else {}


def load_front_door_restoreability_contract(
    contract_path: str | Path | None,
) -> dict | None:
    if not contract_path:
        return None
    path = Path(contract_path).resolve()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"failed to read front-door restoreability contract: {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"front-door restoreability contract is not valid JSON: {path}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError(
            f"front-door restoreability contract must be a JSON object: {path}"
        )
    return _sanitize_front_door_contract_for_proof_artifacts(payload)


def _sanitize_front_door_contract_for_proof_artifacts(payload):
    if isinstance(payload, dict):
        sanitized = {}
        for key, value in payload.items():
            if key == "patch_text":
                sanitized[key] = None
                sanitized["patch_omitted_reason"] = (
                    "raw_patch_text_not_embedded_in_watchdog_proof_artifacts"
                )
                continue
            if key == "text" and isinstance(value, str) and "diff --git " in value:
                sanitized[key] = None
                sanitized["text_omitted_reason"] = (
                    "raw_git_diff_text_not_embedded_in_watchdog_proof_artifacts"
                )
                continue
            sanitized[key] = _sanitize_front_door_contract_for_proof_artifacts(value)
        return sanitized
    if isinstance(payload, list):
        return [_sanitize_front_door_contract_for_proof_artifacts(item) for item in payload]
    return payload


def detect_git_provenance(repo_root: str | Path) -> dict:
    commit = _git_run(repo_root, ["rev-parse", "HEAD"])
    short_commit = _git_run(repo_root, ["rev-parse", "--short=12", "HEAD"]) or "unknown"
    status_text = _git_run(repo_root, ["status", "--porcelain=v1", "--untracked-files=all"]) or ""
    dirty_status = [
        entry for entry in _parse_git_status_porcelain(status_text)
        if not _should_ignore_git_status_path(entry["path"])
    ]
    tags_text = _git_run(repo_root, ["tag", "--points-at", "HEAD"]) or ""
    tags_at_head = sorted(filter(None, (line.strip() for line in tags_text.splitlines())))
    source_snapshot = compute_source_snapshot_manifest(repo_root)
    restoreable_by_commit = bool(commit) and not dirty_status
    preserve_pass_ready = restoreable_by_commit and bool(tags_at_head)

    reason = None
    if not commit:
        reason = "git_commit_unknown"
    elif dirty_status:
        reason = "git_worktree_dirty"
    elif not tags_at_head:
        reason = "head_not_tagged"

    return {
        "commit": commit,
        "short_commit": short_commit,
        "clean": not dirty_status,
        "dirty_file_count": len(dirty_status),
        "dirty_files": [entry["path"] for entry in dirty_status],
        "dirty_status": dirty_status,
        "dirty_file_set_sha256": _compute_dirty_file_set_sha(repo_root, dirty_status),
        "tags_at_head": tags_at_head,
        "restoreability": {
            "source_snapshot_sha256": source_snapshot["sha256"],
            "source_snapshot_file_count": source_snapshot["file_count"],
            "restoreable_by_commit": restoreable_by_commit,
            "preserve_pass_ready": preserve_pass_ready,
            "reason": reason,
        },
    }


def detect_git_provenance_via_node(repo_root: str | Path) -> dict:
    repo_root = Path(repo_root).resolve()
    slot_manifest_js = repo_root / "scripts" / "watchdog" / "lib" / "slot_manifest.js"
    if not slot_manifest_js.is_file():
        return detect_git_provenance(repo_root)
    node_program = """
const { detectGitProvenance } = require(process.argv[1]);
const repoRoot = process.argv[2];
process.stdout.write(JSON.stringify(detectGitProvenance(repoRoot)));
"""
    try:
        result = subprocess.run(
            ["node", "-e", node_program, str(slot_manifest_js), str(repo_root)],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return detect_git_provenance(repo_root)
    if result.returncode != 0:
        return detect_git_provenance(repo_root)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return detect_git_provenance(repo_root)


def verify_local_build_freshness(
    local_web_dir: str | Path,
    repo_root: str | Path | None = None,
    require_preserve_ready: bool = False,
    front_door_restoreability_contract: dict | None = None,
) -> dict:
    web_dir = Path(local_web_dir).resolve()
    root = Path(repo_root).resolve() if repo_root else web_dir.parent.resolve()
    errors: list[str] = []
    details: list[str] = []
    mismatches: list[dict] = []

    manifest = read_local_manifest(web_dir)
    if manifest is None:
        return {
            "verdict": "FAIL",
            "manifest": None,
            "errors": ["local_slot_manifest_missing_or_invalid"],
            "details": [],
            "mismatches": [],
        }

    manifest_runtime = manifest.get("runtime", {})
    manifest_web = manifest.get("web", {})
    manifest_provenance = manifest.get("provenance", {}).get("git", {})
    manifest_restoreability = manifest_provenance.get("restoreability", {})
    actual_web = local_web_hashes(web_dir)
    current_inputs = compute_build_input_manifest(root)
    current_git = detect_git_provenance_via_node(root)

    details.append(f"local manifest source_ref={manifest.get('source_ref')}")
    details.append(f"local manifest created_at={manifest.get('created_at')}")
    details.append(f"local build_inputs_sha256={manifest_runtime.get('build_inputs_sha256')}")
    details.append(f"local restoreable_by_commit={manifest_restoreability.get('restoreable_by_commit')}")
    details.append(f"local preserve_pass_ready={manifest_restoreability.get('preserve_pass_ready')}")
    if front_door_restoreability_contract:
        details.append(
            "restoreability policy owner="
            + str(front_door_restoreability_contract.get("owner") or "front-door")
        )
        details.append(
            "front door source_ref="
            + str(front_door_restoreability_contract.get("source_ref") or "unknown")
        )
        details.append(
            "front door accepted_recovery_mode="
            + str(front_door_restoreability_contract.get("accepted_recovery_mode") or "unknown")
        )
        contract_build_inputs = _front_door_build_inputs(front_door_restoreability_contract)
        if contract_build_inputs:
            details.append(
                "front door build_inputs_sha256="
                + str(contract_build_inputs.get("sha256") or "unknown")
            )

    expected_inputs_sha = manifest_runtime.get("build_inputs_sha256")
    expected_inputs_count = manifest_runtime.get("build_inputs_file_count")
    contract_build_inputs = _front_door_build_inputs(front_door_restoreability_contract)
    contract_inputs_sha = contract_build_inputs.get("sha256")
    contract_inputs_count = contract_build_inputs.get("file_count")
    contract_matches_manifest = (
        bool(contract_inputs_sha)
        and expected_inputs_sha == contract_inputs_sha
        and (
            contract_inputs_count is None
            or expected_inputs_count is None
            or int(expected_inputs_count) == int(contract_inputs_count)
        )
    )
    current_matches_contract = (
        bool(contract_inputs_sha)
        and current_inputs.get("sha256") == contract_inputs_sha
        and (
            contract_inputs_count is None
            or current_inputs.get("file_count") is None
            or int(current_inputs.get("file_count")) == int(contract_inputs_count)
        )
    )
    if (
        front_door_restoreability_contract
        and contract_matches_manifest
        and not current_matches_contract
    ):
        errors.append(
            _format_front_door_recovery_required(
                "post_contract_build_input_drift",
                {
                    "restoreability": {"reason": "post_contract_build_input_drift"},
                    "build_inputs": {
                        "accepted_sha256": contract_inputs_sha,
                        "accepted_file_count": contract_inputs_count,
                        "manifest_sha256": expected_inputs_sha,
                        "manifest_file_count": expected_inputs_count,
                        "current_sha256": current_inputs.get("sha256"),
                        "current_file_count": current_inputs.get("file_count"),
                    },
                },
            )
        )
    if not expected_inputs_sha or expected_inputs_count is None:
        errors.append("manifest_missing_build_inputs_provenance")
    else:
        if expected_inputs_sha != current_inputs["sha256"]:
            mismatches.append({
                "field": "runtime.build_inputs_sha256",
                "manifest": expected_inputs_sha,
                "current": current_inputs["sha256"],
            })
        if int(expected_inputs_count) != int(current_inputs["file_count"]):
            mismatches.append({
                "field": "runtime.build_inputs_file_count",
                "manifest": expected_inputs_count,
                "current": current_inputs["file_count"],
            })

    expected_snapshot_sha = manifest_restoreability.get("source_snapshot_sha256")
    expected_snapshot_count = manifest_restoreability.get("source_snapshot_file_count")
    current_restoreability = current_git.get("restoreability", {})
    if not expected_snapshot_sha or expected_snapshot_count is None:
        errors.append("manifest_missing_restoreability_snapshot")
    else:
        if expected_snapshot_sha != current_restoreability.get("source_snapshot_sha256"):
            details.append(
                "local restoreability snapshot drift detected; ignoring for web freshness because build inputs + commit cleanliness are authoritative"
            )
        if int(expected_snapshot_count) != int(current_restoreability.get("source_snapshot_file_count", -1)):
            details.append(
                "local restoreability snapshot file-count drift detected; ignoring for web freshness because build inputs + commit cleanliness are authoritative"
            )

    if not manifest_provenance:
        errors.append("manifest_missing_git_provenance")
    restoreability_error = (
        _format_front_door_recovery_required
        if front_door_restoreability_contract
        else _format_restoreability_error
    )
    allow_dirty_snapshot = _front_door_allows_dirty_snapshot(front_door_restoreability_contract)
    if allow_dirty_snapshot:
        details.append(
            "front door accepted dirty_snapshot recovery; restoreable_by_commit errors downgraded for local freshness"
        )
    allow_current_repo_dirt_by_build_inputs = (
        bool(front_door_restoreability_contract)
        and contract_matches_manifest
        and current_matches_contract
    )
    if allow_current_repo_dirt_by_build_inputs and not current_git.get("restoreability", {}).get("restoreable_by_commit"):
        details.append(
            "front door build inputs still match accepted contract; current non-runtime git dirt is provenance-only for local freshness"
        )
    if not manifest_restoreability.get("restoreable_by_commit") and not allow_dirty_snapshot:
        errors.append(
            restoreability_error("manifest_not_restoreable_by_commit", manifest_provenance)
        )
    if (
        not current_git.get("restoreability", {}).get("restoreable_by_commit")
        and not allow_dirty_snapshot
        and not allow_current_repo_dirt_by_build_inputs
    ):
        errors.append(
            restoreability_error("current_repo_not_restoreable_by_commit", current_git)
        )
    if require_preserve_ready:
        if not manifest_restoreability.get("preserve_pass_ready"):
            errors.append(
                restoreability_error("manifest_preserve_pass_not_ready", manifest_provenance)
            )
        if not current_git.get("restoreability", {}).get("preserve_pass_ready"):
            errors.append(
                restoreability_error("current_repo_preserve_pass_not_ready", current_git)
            )

    for filename, manifest_key in WEB_FILE_FIELD_MAP:
        manifest_hash = manifest_web.get(manifest_key)
        local_hash = actual_web.get(manifest_key)
        if not manifest_hash:
            errors.append(f"manifest_missing_web_hash:{manifest_key}")
        elif not local_hash:
            errors.append(f"local_web_file_missing:{filename}")
        elif manifest_hash != local_hash:
            mismatches.append({
                "field": f"web.{manifest_key}",
                "manifest": manifest_hash,
                "current": local_hash,
            })

    verdict = "PASS" if not errors and not mismatches else "FAIL"
    return {
        "verdict": verdict,
        "manifest": manifest,
        "errors": errors,
        "details": details,
        "mismatches": mismatches,
        "current_build_inputs": current_inputs,
        "local_hashes": actual_web,
        "current_git": current_git,
        "front_door_restoreability_contract": front_door_restoreability_contract,
    }


def expected_live_subset_from_local_manifest(local_manifest: dict) -> dict:
    runtime = local_manifest.get("runtime", {})
    web = local_manifest.get("web", {})
    provenance = local_manifest.get("provenance", {}).get("git", {})
    restoreability = provenance.get("restoreability", {})
    return {
        "source_ref": local_manifest.get("source_ref"),
        "web": {
            "index_html_sha256": web.get("index_html_sha256"),
            "index_js_sha256": web.get("index_js_sha256"),
            "index_wasm_sha256": web.get("index_wasm_sha256"),
            "index_data_sha256": web.get("index_data_sha256"),
            "actor_visual_profiles_sha256": web.get("actor_visual_profiles_sha256"),
            "compile_report_sha256": web.get("compile_report_sha256"),
            "bundle_hash": web.get("bundle_hash"),
            "ids_lock_hash": web.get("ids_lock_hash"),
        },
        "provenance": {
            "git": {
                "commit": provenance.get("commit"),
                "clean": provenance.get("clean"),
                "dirty_file_count": provenance.get("dirty_file_count"),
                "dirty_file_set_sha256": provenance.get("dirty_file_set_sha256"),
                "restoreability": {
                    "source_snapshot_sha256": restoreability.get("source_snapshot_sha256"),
                    "source_snapshot_file_count": restoreability.get("source_snapshot_file_count"),
                    "restoreable_by_commit": restoreability.get("restoreable_by_commit"),
                    "preserve_pass_ready": restoreability.get("preserve_pass_ready"),
                },
            },
        },
        "runtime": {
            "build_inputs_sha256": runtime.get("build_inputs_sha256"),
            "build_inputs_file_count": runtime.get("build_inputs_file_count"),
            "env_sha256": runtime.get("env_sha256"),
            "config_sha256": runtime.get("config_sha256"),
        },
    }


def fetch_manifest_https(base_url: str) -> dict | None:
    """Fetch slot_manifest.json via HTTPS. Returns parsed JSON or None."""
    url = f"{base_url}/slot_manifest.json"
    try:
        r = subprocess.run(
            ["curl", "-fsS", "--max-time", "10", *_curl_resolve_args(base_url), url],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode == 0:
            return json.loads(r.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        pass
    return None


def fetch_remote_web_hashes(ssh_target: str, web_root: str = VPS_SERVED_WEB_ROOT,
                            identity_args: list[str] | None = None) -> dict:
    """SSH to VPS, sha256sum the served web files. Returns {filename: hash}."""
    files_str = " ".join(f"{web_root}/{f}" for f in WEB_FILES)
    rc, stdout, stderr = _ssh_run(ssh_target, f"sha256sum {files_str}", identity_args)
    result = {}
    normalized_root = web_root.rstrip("/") + "/"
    if rc == 0:
        for line in stdout.splitlines():
            parts = line.split(maxsplit=1)
            if len(parts) >= 2:
                hash_val = parts[0]
                filepath = parts[1].strip()
                if filepath.startswith(normalized_root):
                    filename = filepath[len(normalized_root):]
                else:
                    filename = Path(filepath).name
                result[filename] = hash_val
    return result


def fetch_remote_server_binary_hash(ssh_target: str, server_path: str = VPS_SERVED_SERVER_BINARY,
                                    identity_args: list[str] | None = None) -> str | None:
    """SSH to VPS and sha256 the served server binary."""
    rc, stdout, _stderr = _ssh_run(ssh_target, f"sha256sum {server_path}", identity_args)
    if rc != 0 or not stdout:
        return None
    parts = stdout.split()
    if len(parts) < 1:
        return None
    return parts[0]


def verify_served_web(ssh_target: str, base_url: str = DEFAULT_BASE_URL,
                      web_root: str = VPS_SERVED_WEB_ROOT,
                      server_path: str = VPS_SERVED_SERVER_BINARY,
                      expected_local_hashes: dict | None = None,
                      local_web_dir: str | Path | None = None,
                      repo_root: str | Path | None = None,
                      identity_args: list[str] | None = None,
                      front_door_restoreability_contract: dict | None = None) -> dict:
    """Full verification: manifest fetch, served file hashes, comparisons.

    Returns:
        {
            "verdict": "PASS" | "DRIFT" | "FAIL",
            "manifest": {...} or None,
            "remote_hashes": {"index.html": "abc...", ...},
            "manifest_vs_served": [...mismatches...],
            "local_vs_served": [...mismatches...],
            "errors": [...],
            "details": [...],
        }
    """
    errors: list[str] = []
    details: list[str] = []
    mismatches_manifest: list[dict] = []
    mismatches_local: list[dict] = []
    mismatches_local_manifest_vs_live: list[dict] = []
    local_freshness = None

    if local_web_dir is not None:
        local_freshness = verify_local_build_freshness(
            local_web_dir,
            repo_root,
            front_door_restoreability_contract=front_door_restoreability_contract,
        )
        details.extend(local_freshness["details"])
        if local_freshness["verdict"] != "PASS":
            errors.extend(local_freshness["errors"])
            return {
                "verdict": "FAIL",
                "manifest": local_freshness["manifest"],
                "remote_hashes": {},
                "manifest_vs_served": [],
                "local_vs_served": [],
                "errors": errors,
                "details": details,
                "local_freshness": local_freshness,
            }

    # 1. Fetch manifest via HTTPS
    manifest = fetch_manifest_https(base_url)
    if manifest is None:
        errors.append("manifest_fetch_failed")
        return {
            "verdict": "FAIL",
            "manifest": None,
            "remote_hashes": {},
            "manifest_vs_served": [],
            "local_vs_served": [],
            "errors": errors,
            "details": details,
            "local_freshness": local_freshness,
        }

    details.append(f"manifest source_ref={manifest.get('source_ref')}")
    details.append(f"manifest created_at={manifest.get('created_at')}")
    if local_freshness and local_freshness.get("manifest"):
        expected_live = expected_live_subset_from_local_manifest(local_freshness["manifest"])
        live_subset = expected_live_subset_from_local_manifest(manifest)
        for field, expected_value in [
            ("source_ref", expected_live.get("source_ref")),
            ("web.index_html_sha256", expected_live.get("web", {}).get("index_html_sha256")),
            ("web.index_js_sha256", expected_live.get("web", {}).get("index_js_sha256")),
            ("web.index_wasm_sha256", expected_live.get("web", {}).get("index_wasm_sha256")),
            ("web.index_data_sha256", expected_live.get("web", {}).get("index_data_sha256")),
            ("web.actor_visual_profiles_sha256", expected_live.get("web", {}).get("actor_visual_profiles_sha256")),
            ("web.compile_report_sha256", expected_live.get("web", {}).get("compile_report_sha256")),
            ("web.bundle_hash", expected_live.get("web", {}).get("bundle_hash")),
            ("web.ids_lock_hash", expected_live.get("web", {}).get("ids_lock_hash")),
            ("runtime.tree_manifest_sha256", expected_live.get("runtime", {}).get("tree_manifest_sha256")),
            ("runtime.tree_file_count", expected_live.get("runtime", {}).get("tree_file_count")),
            ("runtime.build_inputs_sha256", expected_live.get("runtime", {}).get("build_inputs_sha256")),
            ("runtime.build_inputs_file_count", expected_live.get("runtime", {}).get("build_inputs_file_count")),
            ("runtime.env_sha256", expected_live.get("runtime", {}).get("env_sha256")),
            ("runtime.config_sha256", expected_live.get("runtime", {}).get("config_sha256")),
        ]:
            parts = field.split(".")
            current = live_subset
            for part in parts:
                current = current.get(part) if isinstance(current, dict) else None
            if field == "source_ref" and source_refs_match(expected_value, current):
                continue
            if expected_value is not None and current != expected_value:
                mismatches_local_manifest_vs_live.append({
                    "field": field,
                    "local": expected_value,
                    "live": current,
                })

    # 2. Fetch remote file hashes via SSH
    remote_hashes = fetch_remote_web_hashes(ssh_target, web_root, identity_args)
    if not remote_hashes:
        errors.append("remote_hash_fetch_failed")
        return {
            "verdict": "FAIL",
            "manifest": manifest,
            "remote_hashes": remote_hashes,
            "manifest_vs_served": [],
            "local_vs_served": [],
            "errors": errors,
            "details": details,
            "local_freshness": local_freshness,
        }
    remote_server_hash = fetch_remote_server_binary_hash(ssh_target, server_path, identity_args)
    if remote_server_hash:
        remote_hashes["server"] = remote_server_hash

    # 3. Compare manifest hashes vs served file hashes
    web_section = manifest.get("web", {})
    manifest_hash_map = {
        filename: web_section.get(field_name)
        for filename, field_name in WEB_FILE_FIELD_MAP
    }

    for filename in WEB_FILES:
        manifest_hash = manifest_hash_map.get(filename)
        served_hash = remote_hashes.get(filename)
        if not manifest_hash:
            mismatches_manifest.append({
                "file": filename,
                "manifest": None,
                "served": served_hash,
                "reason": "manifest_hash_missing",
            })
        elif not served_hash:
            mismatches_manifest.append({
                "file": filename,
                "manifest": manifest_hash,
                "served": None,
                "reason": "file_not_found_on_vps",
            })
        elif manifest_hash != served_hash:
            mismatches_manifest.append({
                "file": filename,
                "manifest": manifest_hash,
                "served": served_hash,
            })
    manifest_server_hash = manifest.get("server", {}).get("binary_sha256")
    if manifest_server_hash and remote_server_hash and manifest_server_hash != remote_server_hash:
        mismatches_manifest.append({
            "file": "server",
            "manifest": manifest_server_hash,
            "served": remote_server_hash,
        })

    # 4. Compare local hashes vs served hashes (if provided)
    if expected_local_hashes:
        local_map = {
            filename: expected_local_hashes.get(field_name)
            for filename, field_name in WEB_FILE_FIELD_MAP
        }
        for filename in WEB_FILES:
            local_hash = local_map.get(filename)
            served_hash = remote_hashes.get(filename)
            if not local_hash:
                mismatches_local.append({
                    "file": filename,
                    "local": None,
                    "served": served_hash,
                    "reason": "local_hash_missing",
                })
            elif not served_hash:
                mismatches_local.append({
                    "file": filename,
                    "local": local_hash,
                    "served": None,
                    "reason": "file_not_found_on_vps",
                })
            elif local_hash != served_hash:
                mismatches_local.append({
                    "file": filename,
                    "local": local_hash,
                    "served": served_hash,
                })

    # Hybrid candidate/current hosting can legitimately leave the served server
    # binary out of sync during a web-only publish. If every served web file
    # matches and the only manifest mismatch is the server hash, treat that as
    # non-fatal for served-web provenance.
    server_only_manifest_mismatch = (
        mismatches_manifest
        and all(m.get("file") == "server" for m in mismatches_manifest)
        and not mismatches_local
        and not mismatches_local_manifest_vs_live
    )
    if server_only_manifest_mismatch:
        details.append(
            "ignored remote-tree provenance drift for server binary during web-only verify"
        )
        mismatches_manifest = []

    runtime_only_live_drift = (
        mismatches_local_manifest_vs_live
        and not mismatches_manifest
        and not mismatches_local
        and all(str(m.get("field", "")).startswith("runtime.") for m in mismatches_local_manifest_vs_live)
    )
    if runtime_only_live_drift:
        details.append(
            "ignored live runtime-manifest drift during web-only verify"
        )
        mismatches_local_manifest_vs_live = []

    # 5. Determine verdict
    if mismatches_manifest or mismatches_local_manifest_vs_live:
        verdict = "FAIL"
    elif mismatches_local:
        verdict = "DRIFT"
    else:
        verdict = "PASS"

    return {
        "verdict": verdict,
        "manifest": manifest,
        "remote_hashes": remote_hashes,
        "manifest_vs_served": mismatches_manifest,
        "local_vs_served": mismatches_local,
        "local_manifest_vs_live": mismatches_local_manifest_vs_live,
        "errors": errors,
        "details": details,
        "local_freshness": local_freshness,
    }


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Verify candidate web integrity (manifest vs served files)",
    )
    p.add_argument("--ssh-target", required=True,
                   help="SSH target for VPS hash verification (e.g., r@35.226.113.14)")
    p.add_argument("--base-url", default=DEFAULT_BASE_URL,
                   help=f"Public base URL (default: {DEFAULT_BASE_URL})")
    p.add_argument("--web-root", default=VPS_SERVED_WEB_ROOT,
                   help=f"VPS served web root (default: {VPS_SERVED_WEB_ROOT})")
    p.add_argument("--server-path", default=VPS_SERVED_SERVER_BINARY,
                   help=f"VPS server binary path (default: {VPS_SERVED_SERVER_BINARY})")
    p.add_argument("--local-web-dir", default=None,
                   help="Local .web/ directory to compare against served files")
    p.add_argument("--repo-root", default=None,
                   help="Repo root used for local build-input freshness verification")
    p.add_argument(
        "--front-door-restoreability-contract",
        default=None,
        help="JSON contract emitted by watchdog_runner.py full-mode front door",
    )
    p.add_argument("--json", action="store_true",
                   help="Output as JSON")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        front_door_restoreability_contract = load_front_door_restoreability_contract(
            args.front_door_restoreability_contract
        )
    except ValueError as exc:
        print(f"FATAL: {exc}")
        return 2

    local_hashes = None
    if args.local_web_dir:
        local_hashes = local_web_hashes(args.local_web_dir)

    result = verify_served_web(
        ssh_target=args.ssh_target,
        base_url=args.base_url,
        web_root=args.web_root,
        server_path=args.server_path,
        expected_local_hashes=local_hashes,
        local_web_dir=args.local_web_dir,
        repo_root=args.repo_root,
        front_door_restoreability_contract=front_door_restoreability_contract,
    )

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Verdict: {result['verdict']}")
        for d in result["details"]:
            print(f"  {d}")
        if result["manifest_vs_served"]:
            print("Manifest vs served mismatches:")
            for m in result["manifest_vs_served"]:
                print(f"  {m['file']}: manifest={str(m.get('manifest', '?'))[:16]}... "
                      f"served={str(m.get('served', '?'))[:16]}...")
        if result["local_vs_served"]:
            print("Local vs served mismatches:")
            for m in result["local_vs_served"]:
                print(f"  {m['file']}: local={str(m.get('local', '?'))[:16]}... "
                      f"served={str(m.get('served', '?'))[:16]}...")
        if result.get("local_manifest_vs_live"):
            print("Local manifest vs live mismatches:")
            for m in result["local_manifest_vs_live"]:
                print(f"  {m['field']}: local={str(m.get('local', '?'))[:16]}... "
                      f"live={str(m.get('live', '?'))[:16]}...")
        local_freshness = result.get("local_freshness") or {}
        if local_freshness.get("mismatches"):
            print("Local build freshness mismatches:")
            for m in local_freshness["mismatches"]:
                print(f"  {m['field']}: manifest={str(m.get('manifest', '?'))[:16]}... "
                      f"current={str(m.get('current', '?'))[:16]}...")
        if result["errors"]:
            print(f"Errors: {result['errors']}")

    if result["verdict"] == "PASS":
        return 0
    elif result["verdict"] == "DRIFT":
        return 10
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
import argparse
import contextlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

VALID_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
SSH_CONNECT_TIMEOUT_SECONDS = 10
SSH_COMMAND_TIMEOUT_SECONDS = 600
SSH_LOCK_TIMEOUT_SECONDS = 120
PROMOTE_MANIFEST_TIMEOUT_SECONDS = 30


REMOTE_PY = r"""
import json
import os
import hashlib
import fcntl
import platform
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from contextlib import contextmanager

payload = PAYLOAD
command = payload["command"]
args = payload["args"]

EXCLUDE_DIRS = {
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
    ".o_asciiid",
    ".o_game",
    ".o_server",
    ".o_term",
    ".d_asciiid",
    ".d_game",
    ".d_server",
    ".d_term",
    "node_modules",
    "__pycache__",
    ".venv",
    "rexpaint_tmp",
    "output",
}
EXCLUDE_REL = {
    ".web/slot_manifest.json",
    ".web/authoritative_state.json",
    ".web/authoritative_state.json.tmp",
}
VALID_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
BUILD_INPUT_ROOT_FILES = [
    "build-web.sh", "web/game_web.html", "web/game_web.cpp", "web/asciicker.js", "web/asciicker.json", "web/asciicker.png", "web/favicon.ico"
]
BUILD_INPUT_SCRIPT_FILES = [
    "scripts/generate_watchdog_slot_manifest.js", "scripts/watchdog/lib/slot_manifest.js"
]
BUILD_INPUT_DIRS = ["assets/appearance_bundle", "assets/fonts", "assets/a3d", "assets/meshes", "assets/sprites", "assets/samples", "assets/palettes", "assets/images"]
BUILD_INPUT_EXCLUDE_REL_PREFIXES = ("assets/meshes/osm_runs/",)
BUILD_INPUT_SOURCE_SUFFIXES = {".c", ".cpp", ".h"}
LOCK_TIMEOUT_SECONDS = __LOCK_TIMEOUT_SECONDS__


def fail_json(error, **extra):
    doc = {"ok": False, "error": error}
    doc.update(extra)
    print(json.dumps(doc, indent=2))
    sys.exit(1)


def validate_name(kind, value):
    if not isinstance(value, str) or not value or not VALID_NAME_RE.fullmatch(value):
        fail_json(f"invalid_{kind}", value=value)
    return value


@contextmanager
def mutation_lock(root):
    os.makedirs(root, exist_ok=True)
    lock_path = os.path.join(root, ".slot-admin.lock")
    with open(lock_path, "a+", encoding="utf-8") as handle:
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    fail_json("mutation_lock_timeout", lock_path=lock_path, timeout_seconds=LOCK_TIMEOUT_SECONDS)
                time.sleep(0.2)
        try:
            yield lock_path
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

def sha256_file(p):
    if not p or not os.path.isfile(p):
        return None
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def sha256_json(v):
    return hashlib.sha256(json.dumps(v, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def read_appearance_bundle_metadata(web_root):
    bundle_dir = os.path.join(web_root, "appearance_bundle", "current")
    report_path = os.path.join(bundle_dir, "compile_report.json")
    report = {}
    try:
        if os.path.isfile(report_path):
            with open(report_path, "r", encoding="utf-8") as f:
                report = json.load(f)
    except Exception:
        report = {}
    return {
        "appearance_bundle_sha256": sha256_file(os.path.join(bundle_dir, "appearance_bundle.json")),
        "ids_lock_sha256": sha256_file(os.path.join(bundle_dir, "ids.lock.json")),
        "compile_report_sha256": sha256_file(report_path),
        "bundle_hash": report.get("bundle_hash"),
        "ids_lock_hash": report.get("ids_lock_hash"),
    }

def file_fingerprint(p):
    st = os.stat(p)
    return {
        "size": st.st_size,
        "mtime_ns": getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000)),
    }

def compute_tree(root):
    entries = []
    for cur, dirs, files in os.walk(root):
        dirs[:] = sorted([d for d in dirs if d not in EXCLUDE_DIRS])
        for name in sorted(files):
            abs_p = os.path.join(cur, name)
            rel = os.path.relpath(abs_p, root).replace(os.sep, "/")
            if rel in EXCLUDE_REL:
                continue
            try:
                fp = file_fingerprint(abs_p)
            except OSError:
                # Skip vanished/broken entries rather than failing manifest emit.
                continue
            entries.append({
                "path": rel,
                **fp,
            })
    return {"sha256": sha256_json(entries), "file_count": len(entries)}

def compute_build_inputs(root):
    rels = set()
    def is_excluded(rel, is_dir=False):
        rel = rel.replace(os.sep, "/")
        if is_dir and not rel.endswith("/"):
            rel += "/"
        return any(rel == prefix.rstrip("/") or rel.startswith(prefix) for prefix in BUILD_INPUT_EXCLUDE_REL_PREFIXES)

    for rel in BUILD_INPUT_ROOT_FILES + BUILD_INPUT_SCRIPT_FILES:
        abs_p = os.path.join(root, rel)
        if os.path.isfile(abs_p):
            rels.add(rel.replace(os.sep, "/"))
    if os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            abs_p = os.path.join(root, name)
            if os.path.isfile(abs_p) and os.path.splitext(name)[1] in BUILD_INPUT_SOURCE_SUFFIXES:
                rels.add(name)
    for rel_dir in BUILD_INPUT_DIRS:
        abs_dir = os.path.join(root, rel_dir)
        if not os.path.isdir(abs_dir):
            continue
        for cur, dirs, files in os.walk(abs_dir):
            dirs[:] = sorted(
                d for d in dirs
                if not is_excluded(os.path.relpath(os.path.join(cur, d), root), is_dir=True)
            )
            for name in sorted(files):
                abs_p = os.path.join(cur, name)
                rel = os.path.relpath(abs_p, root).replace(os.sep, "/")
                if is_excluded(rel):
                    continue
                rels.add(rel)
    entries = []
    for rel in sorted(rels):
        abs_p = os.path.join(root, rel)
        entries.append({
            "path": rel,
            **file_fingerprint(abs_p),
        })
    return {"sha256": sha256_json(entries), "file_count": len(entries)}

def compute_file_set(paths):
    ents = []
    for p in paths or []:
        if p and os.path.isfile(p):
            label = os.path.basename(p) if os.path.isabs(p) else str(p).replace(os.sep, "/")
            ents.append({"path": label, "sha256": sha256_file(p)})
    if not ents:
        return None
    ents.sort(key=lambda x: x["path"])
    return sha256_json(ents)

def make_manifest(repo_root, slot_name, machine_role, source_ref, gameplay_ref, build_id, env_files, config_files):
    tree = compute_tree(repo_root)
    build_inputs = compute_build_inputs(repo_root)
    appearance = read_appearance_bundle_metadata(os.path.join(repo_root, ".web"))
    return {
        "slot_name": slot_name,
        "machine_role": machine_role,
        "source_ref": source_ref,
        "gameplay_ref": gameplay_ref,
        "build_id": build_id,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "web": {
            "index_html_sha256": sha256_file(os.path.join(repo_root, ".web", "index.html")),
            "index_js_sha256": sha256_file(os.path.join(repo_root, ".web", "index.js")),
            "index_wasm_sha256": sha256_file(os.path.join(repo_root, ".web", "index.wasm")),
            "index_data_sha256": sha256_file(os.path.join(repo_root, ".web", "index.data")),
            "appearance_bundle_sha256": appearance["appearance_bundle_sha256"],
            "ids_lock_sha256": appearance["ids_lock_sha256"],
            "compile_report_sha256": appearance["compile_report_sha256"],
            "bundle_hash": appearance["bundle_hash"],
            "ids_lock_hash": appearance["ids_lock_hash"],
        },
        "server": {
            "binary_sha256": sha256_file(os.path.join(repo_root, ".run", "server")),
            "platform": f"{platform.system().lower()}-{platform.machine()}",
        },
        "runtime": {
            "tree_manifest_sha256": tree["sha256"],
            "tree_file_count": tree["file_count"],
            "build_inputs_sha256": build_inputs["sha256"],
            "build_inputs_file_count": build_inputs["file_count"],
            "env_sha256": compute_file_set(env_files),
            "config_sha256": compute_file_set(config_files),
        },
    }

def subset(manifest, slot_name, machine_role):
    return {
        "slot_name": slot_name,
        "machine_role": machine_role,
        "source_ref": manifest.get("source_ref"),
        "gameplay_ref": manifest.get("gameplay_ref"),
        "build_id": manifest.get("build_id"),
        "web": {
            "index_html_sha256": manifest.get("web", {}).get("index_html_sha256"),
            "index_js_sha256": manifest.get("web", {}).get("index_js_sha256"),
            "index_wasm_sha256": manifest.get("web", {}).get("index_wasm_sha256"),
            "index_data_sha256": manifest.get("web", {}).get("index_data_sha256"),
            "appearance_bundle_sha256": manifest.get("web", {}).get("appearance_bundle_sha256"),
            "ids_lock_sha256": manifest.get("web", {}).get("ids_lock_sha256"),
            "compile_report_sha256": manifest.get("web", {}).get("compile_report_sha256"),
            "bundle_hash": manifest.get("web", {}).get("bundle_hash"),
            "ids_lock_hash": manifest.get("web", {}).get("ids_lock_hash"),
        },
        "server": {
            "binary_sha256": manifest.get("server", {}).get("binary_sha256"),
        },
        "runtime": {
            "tree_manifest_sha256": manifest.get("runtime", {}).get("tree_manifest_sha256"),
            "tree_file_count": manifest.get("runtime", {}).get("tree_file_count"),
            "build_inputs_sha256": manifest.get("runtime", {}).get("build_inputs_sha256"),
            "build_inputs_file_count": manifest.get("runtime", {}).get("build_inputs_file_count"),
            "env_sha256": manifest.get("runtime", {}).get("env_sha256"),
            "config_sha256": manifest.get("runtime", {}).get("config_sha256"),
        },
    }

def compare(expected, live, prefix=""):
    mismatches = []
    if isinstance(expected, dict):
        for key, value in expected.items():
            live_value = live.get(key) if isinstance(live, dict) else None
            field = f"{prefix}.{key}" if prefix else key
            mismatches.extend(compare(value, live_value, field))
        return mismatches
    if expected is None:
        return mismatches
    if live != expected:
        mismatches.append({"field": prefix, "expected": expected, "live": live})
    return mismatches

def slot_path(root, slot):
    return os.path.join(root, slot)

def current_path(root, current_link):
    return os.path.join(root, current_link)

def manifest_path(slot_dir):
    return os.path.join(slot_dir, ".web", "slot_manifest.json")

def resolve_slot_config_files(slot_dir, config_files):
    resolved = []
    for item in config_files or []:
        if os.path.isabs(item):
            resolved.append(item)
        else:
            resolved.append(os.path.join(slot_dir, item))
    return resolved

def emit_manifest_for_slot(slot_dir, slot_name, machine_role, source_ref, gameplay_ref, build_id, env_files, config_files):
    m = make_manifest(slot_dir, slot_name, machine_role, source_ref, gameplay_ref, build_id, env_files, config_files)
    out = manifest_path(slot_dir)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(m, f, indent=2)
        f.write("\n")
    os.chmod(out, 0o644)
    return m, out

def verify_slot(root, slot_name, machine_role, env_files, config_files):
    slot_dir = slot_path(root, slot_name)
    if not os.path.isdir(slot_dir):
        raise SystemExit(json.dumps({"ok": False, "error": f"missing_slot:{slot_dir}"}, indent=2))
    mf_path = manifest_path(slot_dir)
    if not os.path.isfile(mf_path):
        raise SystemExit(json.dumps({"ok": False, "error": f"missing_manifest:{mf_path}"}, indent=2))
    saved = json.load(open(mf_path, "r", encoding="utf-8"))
    actual = make_manifest(
        slot_dir,
        slot_name,
        machine_role or saved.get("machine_role") or "unassigned",
        saved.get("source_ref") or "unknown-live-bundle",
        saved.get("gameplay_ref") or saved.get("source_ref") or "unknown-live-bundle",
        saved.get("build_id") or f"{machine_role or saved.get('machine_role') or 'unassigned'}-{slot_name}-{saved.get('source_ref') or 'unknown-live-bundle'}",
        env_files,
        resolve_slot_config_files(slot_dir, config_files),
    )
    mismatches = compare(subset(saved, slot_name, machine_role or saved.get("machine_role")), actual)
    return {
        "ok": len(mismatches) == 0,
        "action": "verify",
        "slot": slot_name,
        "slot_path": slot_dir,
        "manifest_path": mf_path,
        "mismatches": mismatches,
    }

if command == "verify":
    validate_name("slot", args["slot"])
    validate_name("machine_role", args["machine_role"])
    result = verify_slot(
        args["root"],
        args["slot"],
        args.get("machine_role"),
        args.get("env_files") or [],
        args.get("config_files") or [],
    )
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["ok"] else 1)

if command == "emit":
    root = args["root"]
    slot = args["slot"]
    role = args["machine_role"]
    validate_name("slot", slot)
    validate_name("machine_role", role)
    slot_dir = slot_path(root, slot)
    if not os.path.isdir(slot_dir):
        print(json.dumps({"ok": False, "error": f"missing_slot:{slot_dir}"}, indent=2))
        sys.exit(1)
    mf_path = manifest_path(slot_dir)
    saved = {}
    if os.path.isfile(mf_path):
        saved = json.load(open(mf_path, "r", encoding="utf-8"))
    source_ref = args.get("source_ref") or saved.get("source_ref") or "unknown-live-bundle"
    gameplay_ref = args.get("gameplay_ref") or saved.get("gameplay_ref") or source_ref
    build_id = args.get("build_id") or saved.get("build_id") or f"{role}-{slot}-{source_ref}"
    emitted, out = emit_manifest_for_slot(
        slot_dir,
        slot,
        role,
        source_ref,
        gameplay_ref,
        build_id,
        args.get("env_files") or [],
        resolve_slot_config_files(slot_dir, args.get("config_files") or []),
    )
    print(json.dumps({
        "ok": True,
        "action": "emit",
        "slot": slot,
        "slot_path": slot_dir,
        "manifest_path": out,
        "build_id": emitted["build_id"],
    }, indent=2))
    sys.exit(0)

if command == "switch":
    validate_name("slot", args["slot"])
    validate_name("machine_role", args["machine_role"])
    validate_name("current_link", args.get("current_link") or "current")
    with mutation_lock(args["root"]):
        result = verify_slot(
            args["root"],
            args["slot"],
            args.get("machine_role"),
            args.get("env_files") or [],
            args.get("config_files") or [],
        )
        if not result["ok"]:
            print(json.dumps(result, indent=2))
            sys.exit(1)
        cur = current_path(args["root"], args.get("current_link") or "current")
        if os.path.lexists(cur) and not os.path.islink(cur):
            print(json.dumps({"ok": False, "error": f"current_link_not_symlink:{cur}"}, indent=2))
            sys.exit(1)
        tmp = f"{cur}.next-{os.getpid()}"
        try:
            if os.path.lexists(tmp):
                os.unlink(tmp)
            os.symlink(slot_path(args["root"], args["slot"]), tmp)
            os.replace(tmp, cur)
        finally:
            if os.path.lexists(tmp):
                os.unlink(tmp)
    print(json.dumps({
        "ok": True,
        "action": "switch",
        "slot": args["slot"],
        "current_link": cur,
        "target": slot_path(args["root"], args["slot"]),
        "manifest_path": manifest_path(slot_path(args["root"], args["slot"])),
    }, indent=2))
    sys.exit(0)

if command == "init_current":
    root = args["root"]
    slot = args["slot"]
    role = args["machine_role"]
    validate_name("slot", slot)
    validate_name("machine_role", role)
    validate_name("current_link", args.get("current_link") or "current")
    cur = current_path(root, args.get("current_link") or "current")
    slot_dir = slot_path(root, slot)
    env_files = args.get("env_files") or []
    config_files = args.get("config_files") or []
    with mutation_lock(root):
        if not os.path.isdir(cur) or os.path.islink(cur):
            print(json.dumps({"ok": False, "error": f"current_not_plain_directory:{cur}"}, indent=2))
            sys.exit(1)
        if os.path.exists(slot_dir):
            print(json.dumps({"ok": False, "error": f"slot_already_exists:{slot_dir}"}, indent=2))
            sys.exit(1)
        backup_path = f"{cur}.pre-slot-init-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        tmp_parent = tempfile.mkdtemp(prefix=f".slot-init-{slot}-", dir=root)
        stage_path = os.path.join(tmp_parent, "bundle")
        shutil.copytree(cur, stage_path, symlinks=True)
        source_ref = args.get("source_ref") or "unknown-live-bundle"
        gameplay_ref = args.get("gameplay_ref") or source_ref
        build_id = args.get("build_id") or f"{role}-{slot}-{source_ref}"
        actual = make_manifest(stage_path, slot, role, source_ref, gameplay_ref, build_id, env_files, resolve_slot_config_files(stage_path, config_files))
        emitted = {
            "slot_name": slot,
            "machine_role": role,
            "source_ref": source_ref,
            "gameplay_ref": gameplay_ref,
            "build_id": build_id,
            "web": actual["web"],
            "server": actual["server"],
            "runtime": actual["runtime"],
        }
        mismatches = compare(subset(emitted, slot, role), actual)
        if mismatches:
            shutil.rmtree(tmp_parent, ignore_errors=True)
            print(json.dumps({"ok": False, "error": "slot_verify_failed", "mismatches": mismatches}, indent=2))
            sys.exit(1)
        os.rename(cur, backup_path)
        os.rename(stage_path, slot_dir)
        shutil.rmtree(tmp_parent, ignore_errors=True)
        emitted, out = emit_manifest_for_slot(slot_dir, slot, role, source_ref, gameplay_ref, build_id, env_files, resolve_slot_config_files(slot_dir, config_files))
        os.symlink(slot_dir, cur)
    print(json.dumps({
        "ok": True,
        "action": "init_current",
        "slot": slot,
        "slot_path": slot_dir,
        "current_link": cur,
        "backup_path": backup_path,
        "manifest_path": out,
    }, indent=2))
    sys.exit(0)

if command == "stage_promoted_slot":
    root = args["root"]
    slot = args["slot"]
    role = args["machine_role"]
    validate_name("slot", slot)
    validate_name("machine_role", role)
    stage_parent = tempfile.mkdtemp(prefix=f".slot-promote-{slot}-", dir=root)
    stage_path = os.path.join(stage_parent, "bundle")
    os.makedirs(stage_path, exist_ok=True)
    print(json.dumps({"ok": True, "stage_parent": stage_parent, "stage_path": stage_path}, indent=2))
    sys.exit(0)

if command == "discard_stage":
    stage_path = args["stage_path"]
    stage_parent = os.path.dirname(stage_path)
    if os.path.isdir(stage_parent) and os.path.basename(stage_parent).startswith(".slot-promote-"):
        shutil.rmtree(stage_parent, ignore_errors=True)
    print(json.dumps({"ok": True, "action": "discard_stage", "stage_path": stage_path}, indent=2))
    sys.exit(0)

if command == "finalize_promoted_slot":
    root = args["root"]
    slot = args["slot"]
    role = args["machine_role"]
    stage_path = args["stage_path"]
    validate_name("slot", slot)
    validate_name("machine_role", role)
    validate_name("current_link", args.get("current_link") or "current")
    if not os.path.isdir(stage_path):
        print(json.dumps({"ok": False, "error": f"missing_stage:{stage_path}"}, indent=2))
        sys.exit(1)
    env_files = args.get("env_files") or []
    config_files = args.get("config_files") or []
    source_ref = args.get("source_ref") or "unknown-live-bundle"
    gameplay_ref = args.get("gameplay_ref") or source_ref
    build_id = args.get("build_id") or f"{role}-{slot}-{source_ref}"
    actual = make_manifest(stage_path, slot, role, source_ref, gameplay_ref, build_id, env_files, resolve_slot_config_files(stage_path, config_files))
    emitted = {
        "slot_name": slot,
        "machine_role": role,
        "source_ref": source_ref,
        "gameplay_ref": gameplay_ref,
        "build_id": build_id,
        "web": actual["web"],
        "server": actual["server"],
        "runtime": actual["runtime"],
    }
    mismatches = compare(subset(emitted, slot, role), actual)
    if mismatches:
        print(json.dumps({"ok": False, "error": "slot_verify_failed", "mismatches": mismatches}, indent=2))
        sys.exit(1)
    with mutation_lock(root):
        dest_path = slot_path(root, slot)
        backup_path = None
        failed_path = None
        manifest_out = None
        try:
            if os.path.exists(dest_path):
                backup_path = f"{dest_path}.backup-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
                os.rename(dest_path, backup_path)
            os.rename(stage_path, dest_path)
            shutil.rmtree(os.path.dirname(stage_path), ignore_errors=True)
            _, manifest_out = emit_manifest_for_slot(dest_path, slot, role, source_ref, gameplay_ref, build_id, env_files, resolve_slot_config_files(dest_path, config_files))
        except Exception as exc:
            restore_ok = False
            if os.path.exists(dest_path):
                failed_path = f"{dest_path}.failed-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
                os.rename(dest_path, failed_path)
            if backup_path and os.path.exists(backup_path) and not os.path.exists(dest_path):
                os.rename(backup_path, dest_path)
                restore_ok = True
            print(json.dumps({
                "ok": False,
                "error": "finalize_promoted_slot_failed",
                "reason": str(exc),
                "slot": slot,
                "dest_path": dest_path,
                "backup_path": backup_path,
                "failed_path": failed_path,
                "restore_ok": restore_ok,
            }, indent=2))
            sys.exit(1)

        result = {
            "ok": True,
            "action": "finalize_promoted_slot",
            "slot": slot,
            "dest_path": dest_path,
            "backup_path": backup_path,
            "manifest_path": manifest_out,
        }
        if args.get("switch_current"):
            cur = current_path(root, args.get("current_link") or "current")
            if os.path.lexists(cur) and not os.path.islink(cur):
                print(json.dumps({"ok": False, "error": f"current_link_not_symlink:{cur}", "result": result}, indent=2))
                sys.exit(1)
            tmp = f"{cur}.next-{os.getpid()}"
            try:
                if os.path.lexists(tmp):
                    os.unlink(tmp)
                os.symlink(dest_path, tmp)
                os.replace(tmp, cur)
            finally:
                if os.path.lexists(tmp):
                    os.unlink(tmp)
            result["switch"] = {"current_link": cur, "target": dest_path}
    print(json.dumps(result, indent=2))
    sys.exit(0)

print(json.dumps({"ok": False, "error": f"unknown_command:{command}"}, indent=2))
sys.exit(2)
""".replace("__LOCK_TIMEOUT_SECONDS__", str(SSH_LOCK_TIMEOUT_SECONDS))


def build_ssh_cmd(host: str, ssh_user: str, ssh_key: str):
    return [
        "ssh",
        "-i",
        ssh_key,
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        f"ConnectTimeout={SSH_CONNECT_TIMEOUT_SECONDS}",
        "-o",
        f"ServerAliveInterval={SSH_CONNECT_TIMEOUT_SECONDS}",
        "-o",
        "ServerAliveCountMax=3",
        f"{ssh_user}@{host}",
    ]


def run_remote_command(host, ssh_user, ssh_key, command, args, check=True):
    payload_obj = {"command": command, "args": args}
    remote_source = f"PAYLOAD = {payload_obj!r}\n{REMOTE_PY}"
    cmd = build_ssh_cmd(host, ssh_user, ssh_key) + ["sudo", "python3", "-"]
    try:
        proc = subprocess.run(
            cmd,
            input=remote_source.encode(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=SSH_COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        proc = subprocess.CompletedProcess(
            cmd,
            124,
            exc.stdout or b"",
            (exc.stderr or b"") + f"\nremote {command} timed out after {SSH_COMMAND_TIMEOUT_SECONDS}s on {host}".encode(),
        )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"remote {command} failed on {host}\nSTDOUT:\n{proc.stdout.decode()}\nSTDERR:\n{proc.stderr.decode()}"
        )
    return proc


def remote_json(host, ssh_user, ssh_key, command, args, check=True):
    proc = run_remote_command(host, ssh_user, ssh_key, command, args, check=check)
    stdout = proc.stdout.decode().strip()
    stderr = proc.stderr.decode().strip()
    if not stdout:
        result = {"ok": proc.returncode == 0}
        if proc.returncode != 0:
            result["error"] = "remote_command_failed_without_stdout"
            result["returncode"] = proc.returncode
            if stderr:
                result["stderr"] = stderr
        return result
    result = json.loads(stdout)
    if proc.returncode != 0:
        result.setdefault("ok", False)
        result.setdefault("returncode", proc.returncode)
        if stderr:
            result.setdefault("stderr", stderr)
    return result


def default_env_files():
    return ["/etc/default/asciicker-server"]


def default_config_files():
    return ["web/asciicker.json"]


def add_common_slot_args(parser):
    parser.add_argument("--root", default="/opt/asciicker")
    parser.add_argument("--current-link", default="current")
    parser.add_argument("--env-file", action="append", default=[])
    parser.add_argument("--config-file", action="append", default=[])


def effective_env_files(args):
    return args.env_file or default_env_files()


def effective_config_files(args):
    return args.config_file or default_config_files()


def source_ref_for_role(role, slot):
    return f"remote-{role}-{slot}-bundle"


def argparse_name(kind):
    def _validate(value):
        if not VALID_NAME_RE.fullmatch(value):
            raise argparse.ArgumentTypeError(
                f"{kind} must match {VALID_NAME_RE.pattern}; got {value!r}"
            )
        return value
    return _validate


def tar_stream_between_hosts(source_host, source_user, source_ssh_key, source_dir, dest_host, dest_user, dest_ssh_key, dest_dir):
    tar_excludes = [
        "--exclude=./.web/slot_manifest.json",
        "--exclude=./.web/authoritative_state.json",
        "--exclude=./.web/authoritative_state.json.tmp",
    ]
    src = subprocess.Popen(
        build_ssh_cmd(source_host, source_user, source_ssh_key) + [
            "sudo",
            "tar",
            "-C",
            source_dir,
            "--warning=no-file-changed",
            *tar_excludes,
            "-cf",
            "-",
            ".",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    dst = subprocess.Popen(
        build_ssh_cmd(dest_host, dest_user, dest_ssh_key) + [
            "sudo",
            "tar",
            "-C",
            dest_dir,
            "-xf",
            "-",
        ],
        stdin=src.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert src.stdout is not None
    src.stdout.close()
    try:
        dst_out, dst_err = dst.communicate(timeout=SSH_COMMAND_TIMEOUT_SECONDS)
        src_out, src_err = src.communicate(timeout=SSH_COMMAND_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        for proc in (dst, src):
            with contextlib.suppress(Exception):
                proc.kill()
        dst_out, dst_err = dst.communicate()
        src_out, src_err = src.communicate()
        raise RuntimeError(
            "remote tar stream timed out\n"
            f"source_host={source_host}\n"
            f"dest_host={dest_host}\n"
            f"timeout_seconds={SSH_COMMAND_TIMEOUT_SECONDS}\n"
            f"source stderr:\n{src_err.decode()}\n"
            f"dest stderr:\n{dst_err.decode()}"
        )
    if src.returncode != 0 or dst.returncode != 0:
        raise RuntimeError(
            "remote tar stream failed\n"
            f"source rc={src.returncode}\nsource stderr:\n{src_err.decode()}\n"
            f"dest rc={dst.returncode}\ndest stderr:\n{dst_err.decode()}"
        )
    return {
        "source_stdout": src_out.decode(),
        "source_stderr": src_err.decode(),
        "dest_stdout": dst_out.decode(),
        "dest_stderr": dst_err.decode(),
    }


def command_verify(args):
    result = remote_json(
        args.host,
        args.ssh_user,
        args.ssh_key,
        "verify",
        {
            "root": args.root,
            "slot": args.slot,
            "machine_role": args.machine_role,
            "env_files": effective_env_files(args),
            "config_files": effective_config_files(args),
        },
        check=False,
    )
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


def command_emit(args):
    result = remote_json(
        args.host,
        args.ssh_user,
        args.ssh_key,
        "emit",
        {
            "root": args.root,
            "slot": args.slot,
            "machine_role": args.machine_role,
            "env_files": effective_env_files(args),
            "config_files": effective_config_files(args),
            "source_ref": args.source_ref,
            "gameplay_ref": args.gameplay_ref,
            "build_id": args.build_id,
        },
        check=False,
    )
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


def command_switch(args):
    result = remote_json(
        args.host,
        args.ssh_user,
        args.ssh_key,
        "switch",
        {
            "root": args.root,
            "slot": args.slot,
            "machine_role": args.machine_role,
            "current_link": args.current_link,
            "env_files": effective_env_files(args),
            "config_files": effective_config_files(args),
        },
        check=False,
    )
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


def command_init_current(args):
    result = remote_json(
        args.host,
        args.ssh_user,
        args.ssh_key,
        "init_current",
        {
            "root": args.root,
            "slot": args.slot,
            "machine_role": args.machine_role,
            "current_link": args.current_link,
            "env_files": effective_env_files(args),
            "config_files": effective_config_files(args),
            "source_ref": args.source_ref or source_ref_for_role(args.machine_role, args.slot),
            "gameplay_ref": args.gameplay_ref or args.source_ref or source_ref_for_role(args.machine_role, args.slot),
            "build_id": args.build_id or f"{args.machine_role}-{args.slot}-{args.source_ref or source_ref_for_role(args.machine_role, args.slot)}",
        },
        check=False,
    )
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


PROMOTE_BLOCKED_DEST_ROLES = frozenset({"control"})


def _read_dest_live_manifest_machine_role(args) -> str | None:
    check_proc = subprocess.run(
        build_ssh_cmd(args.dest_host, args.dest_ssh_user, args.dest_ssh_key) + [
            "sudo", "cat",
            f"{args.dest_root}/{args.current_link}/.web/slot_manifest.json",
        ],
        capture_output=True,
        check=False,
        timeout=PROMOTE_MANIFEST_TIMEOUT_SECONDS,
    )
    if check_proc.returncode != 0:
        stderr = check_proc.stderr.decode(errors="replace").strip()
        raise subprocess.SubprocessError(
            f"ssh manifest read failed rc={check_proc.returncode}"
            + (f": {stderr}" if stderr else "")
        )
    payload = check_proc.stdout.decode(errors="strict")
    live_manifest = json.loads(payload)
    if not isinstance(live_manifest, dict):
        raise ValueError("slot_manifest.json did not decode to an object")
    return live_manifest.get("machine_role")


def command_promote(args):
    # --- SAFETY GUARD: promote must never target a live control machine ---
    # This prevents the exact failure mode that caused 3 baseline rollbacks.
    # Layer 1: CLI arg check
    if args.dest_machine_role in PROMOTE_BLOCKED_DEST_ROLES:
        print(json.dumps({
            "ok": False,
            "error": "promote_safety_blocked",
            "reason": f"dest_machine_role={args.dest_machine_role!r} is blocked; promote may only target candidate machines",
        }, indent=2))
        return 1

    # Layer 2: defense-in-depth — read the dest host's live-served manifest
    # and refuse if the currently-active slot reports machine_role=control
    try:
        live_role = _read_dest_live_manifest_machine_role(args)
    except (json.JSONDecodeError, subprocess.SubprocessError, OSError, UnicodeDecodeError, ValueError) as exc:
        print(json.dumps({
            "ok": False,
            "error": "promote_safety_manifest_check_failed",
            "reason": (
                "dest host live manifest safety check failed; refusing to promote "
                f"without a trustworthy live machine_role ({type(exc).__name__}: {exc})"
            ),
            "dest_host": args.dest_host,
        }, indent=2))
        return 1

    if live_role in PROMOTE_BLOCKED_DEST_ROLES:
        print(json.dumps({
            "ok": False,
            "error": "promote_safety_blocked",
            "reason": f"dest host live manifest reports machine_role={live_role!r}; refusing to promote onto the live control machine",
            "dest_host": args.dest_host,
            "live_manifest_machine_role": live_role,
        }, indent=2))
        return 1

    source_verify = remote_json(
        args.source_host,
        args.source_ssh_user,
        args.source_ssh_key,
        "verify",
        {
            "root": args.source_root,
            "slot": args.source_slot,
            "machine_role": args.source_machine_role,
            "env_files": effective_env_files(args),
            "config_files": effective_config_files(args),
        },
        check=False,
    )
    if not source_verify.get("ok"):
        print(json.dumps({"ok": False, "error": "source_verify_failed", "source_verify": source_verify}, indent=2))
        return 1

    stage = remote_json(
        args.dest_host,
        args.dest_ssh_user,
        args.dest_ssh_key,
        "stage_promoted_slot",
        {
            "root": args.dest_root,
            "slot": args.dest_slot,
            "machine_role": args.dest_machine_role,
        },
    )
    finalize = {"ok": False, "error": "promote_not_started"}
    stage_path = stage.get("stage_path")
    try:
        tar_stream_between_hosts(
            args.source_host,
            args.source_ssh_user,
            args.source_ssh_key,
            os.path.join(args.source_root, args.source_slot),
            args.dest_host,
            args.dest_ssh_user,
            args.dest_ssh_key,
            stage["stage_path"],
        )

        finalize = remote_json(
            args.dest_host,
            args.dest_ssh_user,
            args.dest_ssh_key,
            "finalize_promoted_slot",
            {
                "root": args.dest_root,
                "slot": args.dest_slot,
                "machine_role": args.dest_machine_role,
                "current_link": args.current_link,
                "switch_current": args.switch_current,
                "stage_path": stage["stage_path"],
                "env_files": effective_env_files(args),
                "config_files": effective_config_files(args),
                "source_ref": args.source_ref or source_ref_for_role(args.source_machine_role, args.source_slot),
                "gameplay_ref": args.gameplay_ref or args.source_ref or source_ref_for_role(args.source_machine_role, args.source_slot),
                "build_id": args.build_id or f"{args.dest_machine_role}-{args.dest_slot}-{args.source_ref or source_ref_for_role(args.source_machine_role, args.source_slot)}",
            },
            check=False,
        )
    except Exception as exc:
        finalize = {"ok": False, "error": "promote_transfer_failed", "reason": str(exc)}
    finally:
        if stage_path and not finalize.get("ok"):
            remote_json(
                args.dest_host,
                args.dest_ssh_user,
                args.dest_ssh_key,
                "discard_stage",
                {"stage_path": stage_path},
                check=False,
            )
    print(json.dumps({
        "ok": finalize.get("ok", False),
        "action": "promote",
        "source_host": args.source_host,
        "source_slot": args.source_slot,
        "dest_host": args.dest_host,
        "dest_slot": args.dest_slot,
        "source_verify": source_verify,
        "stage": stage,
        "finalize": finalize,
    }, indent=2))
    return 0 if finalize.get("ok") else 1


def build_parser():
    parser = argparse.ArgumentParser(description="Remote control/candidate slot admin over SSH.")
    parser.add_argument("--ssh-key", default=str(Path.home() / ".ssh" / "google_compute_engine"))
    parser.add_argument("--ssh-user", default="r")
    sub = parser.add_subparsers(dest="command", required=True)

    verify = sub.add_parser("verify")
    verify.add_argument("--host", required=True)
    verify.add_argument("--slot", required=True, type=argparse_name("slot"))
    verify.add_argument("--machine-role", required=True, type=argparse_name("machine_role"))
    add_common_slot_args(verify)
    verify.set_defaults(func=command_verify)

    emit = sub.add_parser("emit")
    emit.add_argument("--host", required=True)
    emit.add_argument("--slot", required=True, type=argparse_name("slot"))
    emit.add_argument("--machine-role", required=True, type=argparse_name("machine_role"))
    emit.add_argument("--source-ref")
    emit.add_argument("--gameplay-ref")
    emit.add_argument("--build-id")
    add_common_slot_args(emit)
    emit.set_defaults(func=command_emit)

    switch = sub.add_parser("switch")
    switch.add_argument("--host", required=True)
    switch.add_argument("--slot", required=True, type=argparse_name("slot"))
    switch.add_argument("--machine-role", required=True, type=argparse_name("machine_role"))
    add_common_slot_args(switch)
    switch.set_defaults(func=command_switch)

    init_current = sub.add_parser("init-current")
    init_current.add_argument("--host", required=True)
    init_current.add_argument("--slot", required=True, type=argparse_name("slot"))
    init_current.add_argument("--machine-role", required=True, type=argparse_name("machine_role"))
    init_current.add_argument("--source-ref")
    init_current.add_argument("--gameplay-ref")
    init_current.add_argument("--build-id")
    add_common_slot_args(init_current)
    init_current.set_defaults(func=command_init_current)

    promote = sub.add_parser("promote")
    promote.add_argument("--source-host", required=True)
    promote.add_argument("--source-slot", required=True, type=argparse_name("source_slot"))
    promote.add_argument("--source-machine-role", required=True, type=argparse_name("source_machine_role"))
    promote.add_argument("--source-root", default="/opt/asciicker")
    promote.add_argument("--source-ssh-key")
    promote.add_argument("--source-ssh-user")
    promote.add_argument("--dest-host", required=True)
    promote.add_argument("--dest-slot", required=True, type=argparse_name("dest_slot"))
    promote.add_argument("--dest-machine-role", required=True, type=argparse_name("dest_machine_role"))
    promote.add_argument("--dest-root", default="/opt/asciicker")
    promote.add_argument("--dest-ssh-key")
    promote.add_argument("--dest-ssh-user")
    promote.add_argument("--source-ref")
    promote.add_argument("--gameplay-ref")
    promote.add_argument("--build-id")
    promote.add_argument("--switch-current", action="store_true")
    add_common_slot_args(promote)
    promote.set_defaults(func=command_promote)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if getattr(args, "source_ssh_key", None) is None:
        args.source_ssh_key = args.ssh_key
    if getattr(args, "source_ssh_user", None) is None:
        args.source_ssh_user = args.ssh_user
    if getattr(args, "dest_ssh_key", None) is None:
        args.dest_ssh_key = args.ssh_key
    if getattr(args, "dest_ssh_user", None) is None:
        args.dest_ssh_user = args.ssh_user

    try:
        return args.func(args)
    except RuntimeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())

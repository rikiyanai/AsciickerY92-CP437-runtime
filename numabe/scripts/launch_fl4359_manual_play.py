#!/usr/bin/env python3
"""Launch the FL-4359 human-playable Colyseus Godot session.

This is the canonical manual-play surface for perspective review. It is not a
proof wrapper: no scripted input driver, no jitter capture, no exact pose, no
readback package, no analyzer.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.launcher_lib import product_catalog as _product_catalog
from scripts import run_receipt_preservation
from scripts import true_ff_launch_guard as _launch_guard

# Override lets a proof run use a committed server snapshot while another
# session has the working-tree server mid-edit. Default is unchanged.
COLYSEUS_DIR = _product_catalog.resolve_colyseus_dir(ROOT)
_GODOT_PRODUCT = _product_catalog.PRODUCTS["godot"]
GODOT_PROJECT = (ROOT / str(_GODOT_PRODUCT["project_path"])).parent
GODOT_BIN = _product_catalog.resolve_godot_binary() or str(_product_catalog.GODOT_DEFAULT_BINARY)
GODOT_APP = _product_catalog.resolve_godot_app() or _product_catalog.GODOT_DEFAULT_APP
VIEWPORT_CAPTURE_EXIT_ENV = "ASCIICKER_CAPTURE_VIEWPORT_EXIT"
SCREEN_RECORDING_ALLOWED_ENV = "ASCIICKER_SCREEN_RECORDING_ALLOWED"
# FL-4895: proof id and bundle title of a manual session with no --proof-id.
MANUAL_PROOF_ID = "manual"
FL4216_WATER_INTERACTION_FIELD_CONTROL_OFF_ENV = (
    "FL4216_WATER_INTERACTION_FIELD_CONTROL_OFF"
)
MACOS_CHILD_ENV_PREFIXES = (
    "AIRPLANE_",
    "ASCIICKER_",
    "COLYSEUS_",
    "FL",
    "GODOT_",
    "PLAYER_",
)

# LaunchServices starts Godot outside Python's child-process tree. Once that
# process disappears, macOS exposes liveness but not its wait status; treating
# that absence as a clean exit hid native crashes from the manual-run receipt.
DETACHED_GODOT_EXIT_STATUS_UNKNOWN = 125


def _launch_scene_for_user_args(extra_user_args: list[str]) -> str:
    """Choose the initial Godot scene before Main.tscn has a chance to load."""
    return (
        "res://scenes/ui/MainMenu.tscn"
        if "--main-menu" in extra_user_args
        else "res://scenes/Main.tscn"
    )


# FL-4881: the runtime map catalog is the .a3d directory scan, the same rule
# as godot_project/scripts/map/RuntimeMapCatalog.gd map_ids(): sorted stems,
# the default first. The server resolves the same stems in
# colyseus_server/src/sim/terrain.ts mapPathForId.
RUNTIME_MAPS_DIR = GODOT_PROJECT / "assets" / "maps"
DEFAULT_MAP_ID = "game_map_y8"
MAP_ID_PATTERN = re.compile(r"^[a-z0-9_]+$")


def runtime_map_ids(maps_dir: Path | None = None) -> list[str]:
    """Selectable runtime map ids, default first, then sorted."""
    directory = maps_dir or RUNTIME_MAPS_DIR
    ids = sorted(
        path.stem
        for path in directory.glob("*.a3d")
        if path.is_file() and MAP_ID_PATTERN.match(path.stem)
    )
    if DEFAULT_MAP_ID in ids:
        ids.remove(DEFAULT_MAP_ID)
        ids.insert(0, DEFAULT_MAP_ID)
    return ids


def _map_id_arg(value: str) -> str:
    if value not in runtime_map_ids():
        raise argparse.ArgumentTypeError(
            f"unknown map id {value!r}; choose one of: {', '.join(runtime_map_ids())}"
        )
    return value


def godot_map_user_args(map_id: str | None) -> list[str]:
    """Godot user args that select the map; the client sends the same id as
    the Colyseus join option map_id, and the room builds that terrain."""
    return [f"--map-id={map_id}"] if map_id else []


def _authoritative_map_id_from_server_log(log_path: Path) -> str | None:
    """Read the room identity emitted by the authoritative server, if any."""
    try:
        text = log_path.read_text(encoding="utf-8")
    except OSError:
        return None
    matches = re.findall(r"^COLYSEUS_ROOM_MAP_ID map_id=([a-z0-9_]+) ", text, re.MULTILINE)
    return matches[-1] if matches else None


def _frontmost_process_name() -> str:
    """Name of the macOS application process that currently owns the keyboard.

    Returns "" when it cannot be read (non-macOS, missing Accessibility
    permission, osascript failure). An empty answer is an ABSENT observation,
    never a claim that some particular app is frontmost.
    """
    if sys.platform != "darwin":
        return ""
    try:
        completed = subprocess.run(
            [
                "osascript",
                "-e",
                'tell application "System Events" to get name of first '
                "application process whose frontmost is true",
            ],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _frontmost_process_pid() -> int | None:
    """Return the PID of the macOS keyboard owner when observable."""
    if sys.platform != "darwin":
        return None
    try:
        completed = subprocess.run(
            [
                "osascript",
                "-e",
                'tell application "System Events" to get unix id of first '
                "application process whose frontmost is true",
            ],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    raw = completed.stdout.strip()
    return int(raw) if completed.returncode == 0 and raw.isdigit() else None


def _macos_process_registered(pid: int) -> bool:
    """Ask the macOS application authority whether *pid* is registered."""
    if sys.platform != "darwin" or pid <= 0:
        return False
    try:
        completed = subprocess.run(
            [
                "osascript",
                "-e",
                'tell application "System Events" to count '
                f"(application processes whose unix id is {pid})",
            ],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0 and completed.stdout.strip() == "1"


def _activate_macos_window(pid: int, timeout: float = 10.0) -> dict[str, object]:
    """Give the launched Godot process the keyboard, and report whether it worked.

    WHY THIS EXISTS (FL-4359 manual-run keyboard defect): on macOS a process
    spawned from a terminal does NOT inherit frontmost status. The Godot window
    appears, but the terminal emulator keeps keyboard focus, so every WASD press
    an operator makes is delivered to the terminal instead of the game. The
    session then looks like broken input handling when it is really a focus
    owner that was never set. Nothing in this launcher used to claim that owner.

    Returns a receipt rather than a bare bool, because "activation was skipped",
    "activation was attempted and refused", and "activation succeeded" are three
    different states and only the third supports a manual-input claim.
    """
    receipt: dict[str, object] = {
        "attempted": False,
        "platform": sys.platform,
        "target_pid": pid,
        "target_selector": f"System Events unix id {pid}",
        "activation_method": "System Events process frontmost setter",
        "frontmost_before": _frontmost_process_name(),
        "frontmost_after": "",
        "activated": False,
        "reason": "",
        "activation_command_returncode": None,
        "activation_command_stderr": "",
        "registered": _macos_process_registered(pid),
        "frontmost_pid": _frontmost_process_pid(),
    }
    if sys.platform != "darwin":
        receipt["reason"] = "not_macos_no_activation_owner"
        return receipt
    if pid <= 0:
        receipt["reason"] = "invalid_godot_child_pid"
        return receipt
    receipt["attempted"] = True
    deadline = time.monotonic() + timeout
    last_failure = ""
    # The window is not created the instant the process starts, so a single
    # early attempt can succeed on the process while no window exists yet.
    # Retry until System Events reports our pid as frontmost.
    while time.monotonic() < deadline:
        try:
            activation_command = subprocess.run(
                [
                    "osascript",
                    "-e",
                    'tell application "System Events" to set frontmost of '
                    f"(first application process whose unix id is {pid}) to true",
                ],
                capture_output=True,
                text=True,
                timeout=5.0,
            )
        except (OSError, subprocess.TimeoutExpired):
            receipt["reason"] = "osascript_unavailable"
            return receipt
        receipt["activation_command_returncode"] = activation_command.returncode
        receipt["activation_command_stderr"] = (
            getattr(activation_command, "stderr", "") or ""
        ).strip()
        if activation_command.returncode != 0:
            # A process can exist before its application process/window is
            # registered with System Events. Keep retrying during startup, but
            # preserve the concrete failure so an unsuccessful focus claim can
            # never look like successful keyboard ownership.
            last_failure = (
                "activation_command_failed_rc_%s" % activation_command.returncode
            )
        # Verify against the SAME authority that reports focus, rather than
        # trusting the setter's exit code: a granted-but-ineffective activation
        # is exactly the failure this defect was made of.
        try:
            check = subprocess.run(
                [
                    "osascript",
                    "-e",
                    'tell application "System Events" to get unix id of first '
                    "application process whose frontmost is true",
                ],
                capture_output=True,
                text=True,
                timeout=5.0,
            )
        except (OSError, subprocess.TimeoutExpired):
            last_failure = "frontmost_query_unavailable"
            break
        frontmost_pid = (getattr(check, "stdout", "") or "").strip()
        if check.returncode == 0 and frontmost_pid == str(pid):
            receipt["registered"] = _macos_process_registered(pid)
            receipt["frontmost_pid"] = pid
            if receipt["registered"]:
                receipt["activated"] = True
                receipt["reason"] = "registered_godot_process_is_frontmost"
                break
            last_failure = "frontmost_target_not_registered"
        if check.returncode != 0 and not last_failure:
            last_failure = "frontmost_query_failed_rc_%s" % check.returncode
        elif check.returncode == 0 and frontmost_pid and not last_failure:
            last_failure = "frontmost_pid_mismatch_%s" % frontmost_pid
        time.sleep(0.4)
    receipt["frontmost_after"] = _frontmost_process_name()
    if not receipt["activated"] and not receipt["reason"]:
        # Most commonly: the parent terminal lacks Accessibility permission, so
        # System Events refuses. Name it instead of failing the session, because
        # the operator can still click the window to focus it manually.
        receipt["reason"] = last_failure or "activation_refused_or_timed_out"
    return receipt


def _matching_godot_engine_pids(project_path: Path, port: int) -> set[int]:
    """Find only Godot engines launched for this project and server port.

    The project path plus the unique Colyseus port is the launch identity. A
    generic newest-Godot lookup can select the Project Manager or another lane.
    """
    completed = subprocess.run(
        ["ps", "-axo", "pid=,command="],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return set()
    project_token = str(project_path.resolve())
    port_token = f"--port={port}"
    result: set[int] = set()
    for raw_line in completed.stdout.splitlines():
        stripped = raw_line.strip()
        pid_text, separator, command = stripped.partition(" ")
        if not separator or not pid_text.isdigit():
            continue
        if (
            "Godot.app/Contents/MacOS/Godot" in command
            and project_token in command
            and port_token in command
        ):
            result.add(int(pid_text))
    return result


def _macos_forwarded_environment(env: dict[str, str]) -> list[str]:
    """Return non-secret runtime variables that LaunchServices must forward."""
    return sorted(
        key
        for key in env
        if key.startswith(MACOS_CHILD_ENV_PREFIXES)
        and key not in {"GODOT_BIN", "GODOT_APP"}
    )


class _DetachedGodotProcess:
    """Process facade for a LaunchServices child that is not Python's child.

    LaunchServices does not give this launcher a wait handle. A missing PID is
    therefore evidence that the engine stopped, but it is not evidence of a
    successful exit. ``wait()`` returns the launcher's explicit unknown-status
    code in that case, never a fabricated Godot zero exit status.
    """

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.exit_status_known = False
        self.exit_status_reason = "engine_pid_still_running"

    def poll(self) -> int | None:
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            self.exit_status_reason = "detached_engine_disappeared_exit_status_unknown"
            return DETACHED_GODOT_EXIT_STATUS_UNKNOWN
        except PermissionError:
            return None
        return None

    def wait(self, timeout: float | None = None) -> int:
        deadline = None if timeout is None else time.monotonic() + timeout
        while self.poll() is None:
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired([str(GODOT_APP)], timeout)
            time.sleep(0.2)
        return DETACHED_GODOT_EXIT_STATUS_UNKNOWN

    def send_signal(self, sig: int) -> None:
        os.kill(self.pid, sig)

    def kill(self) -> None:
        os.kill(self.pid, signal.SIGKILL)


def _launch_macos_godot_bundle(
    godot_args: list[str],
    env: dict[str, str],
    godot_log: Path,
    project_path: Path,
    port: int,
    background: bool,
    timeout: float = 15.0,
) -> tuple[_DetachedGodotProcess | None, dict[str, object]]:
    """Launch one registered Godot.app instance and resolve its exact engine PID."""
    before = _matching_godot_engine_pids(project_path, port)
    forwarded_keys = _macos_forwarded_environment(env)
    command = ["/usr/bin/open", "-n"]
    if background:
        command.append("-g")
    command.extend(["-a", str(GODOT_APP)])
    for key in forwarded_keys:
        command.extend(["--env", f"{key}={env[key]}"])
    command.extend(["-o", str(godot_log), "--stderr", str(godot_log), "--args"])
    command.extend(godot_args)
    receipt: dict[str, object] = {
        "launch_method": "LaunchServices open -n Godot.app",
        "bundle_path": str(GODOT_APP),
        "open_returncode": None,
        "open_stderr": "",
        "engine_pid": None,
        "preexisting_matching_pids": sorted(before),
        "new_matching_pids": [],
        "forwarded_environment_keys": forwarded_keys,
        "launch_reason": "",
        "cleanup_pids": [],
    }
    if before:
        receipt["launch_reason"] = "preexisting_matching_engine_pids"
        return None, receipt
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=10.0,
    )
    receipt["open_returncode"] = completed.returncode
    receipt["open_stderr"] = completed.stderr.strip()
    if completed.returncode != 0:
        receipt["launch_reason"] = "launchservices_open_failed"
        return None, receipt

    deadline = time.monotonic() + timeout
    candidates: set[int] = set()
    while time.monotonic() < deadline:
        candidates = _matching_godot_engine_pids(project_path, port) - before
        if len(candidates) == 1:
            pid = next(iter(candidates))
            receipt["engine_pid"] = pid
            receipt["new_matching_pids"] = [pid]
            receipt["launch_reason"] = "exact_engine_pid_resolved"
            return _DetachedGodotProcess(pid), receipt
        if len(candidates) > 1:
            break
        time.sleep(0.2)
    receipt["new_matching_pids"] = sorted(candidates)
    receipt["launch_reason"] = (
        "multiple_matching_engine_pids" if len(candidates) > 1
        else "engine_pid_not_found"
    )
    if len(candidates) > 1:
        for pid in sorted(candidates):
            try:
                os.kill(pid, signal.SIGINT)
                receipt["cleanup_pids"].append(pid)
            except ProcessLookupError:
                pass
    return None, receipt


def _manual_child_environment(
    parent_environment: dict[str, str],
) -> dict[str, str]:
    """Remove proof-only controls from a human-playable child.

    Manual play inherits ordinary shell configuration, but it must never inherit
    the explicit capture-and-exit switch or the FL-4216 performance control arm.
    Leaving either variable alive would make a normal player session exercise a
    proof/performance variant instead of the user-facing manual route.
    """

    child_environment = dict(parent_environment)
    child_environment.pop(VIEWPORT_CAPTURE_EXIT_ENV, None)
    child_environment.pop(FL4216_WATER_INTERACTION_FIELD_CONTROL_OFF_ENV, None)
    return child_environment


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _wait_for(predicate, timeout: float, interval: float = 0.25) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _timestamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def _iso_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _git_head() -> str:
    # Read the repository's own ref files so receipt creation cannot interfere
    # with launcher tests that replace subprocess.Popen with a process fake.
    git_dir = ROOT / ".git"
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref: "):
            return (git_dir / head[5:]).read_text(encoding="utf-8").strip()
        return head
    except OSError:
        return ""


# FL-4888: the launcher is the one run-identity owner. The runtime source is
# every path the manual route executes: the Godot project (scripts, shaders,
# assets), the Colyseus authority, the F9 recorder tooling, and this launcher
# with its catalog. Tracked edits and untracked files share one pathspec.
RUNTIME_SOURCE_PATHSPECS = (
    "godot_project",
    "colyseus_server",
    "scripts/recording",
    "scripts/launch_fl4359_manual_play.py",
    "scripts/launcher_lib",
    # Executed by the F9 finalizer (scripts/recording/f9_finalize_bundle.py) and
    # by bundle preservation; a replay from a snapshot runs them from the tree.
    "scripts/build_f10_frames_html.py",
    "scripts/run_receipt_preservation.py",
)
# Durable receipt owner (scripts/run_receipt_preservation.py convention).
# Source snapshots are content-addressed by the runtime source digest, so every
# run that names a digest can rebuild the exact tree it executed.
DURABLE_RECEIPT_ROOT = ROOT / "artifacts" / "run_receipts"
SOURCE_SNAPSHOT_DIRNAME = "source_snapshots"
SOURCE_SNAPSHOT_SCHEMA = "asciicker.runtime_source_snapshot.v2"
RUN_IDENTITY_SCHEMA = "asciicker.run_identity.v1"
RUN_RECEIPT_FL_ID = "FL-4359"


class SourceSnapshotError(RuntimeError):
    """The runtime source could not be identified or preserved."""


def _capture_runtime_source(pathspecs: tuple[str, ...] = RUNTIME_SOURCE_PATHSPECS) -> dict[str, object]:
    """Read the dirty runtime source once, as bytes.

    The digest and the preserved snapshot are computed from the SAME bytes, so
    the preserved tree is exactly the tree the digest names. Raises
    SourceSnapshotError when git cannot answer. Another runner that executes
    more files (scripts/run_fl4359_headed_movement.py and its driver) passes
    a wider pathspec tuple; the launcher's own calls keep the default.
    """
    pathspecs = tuple(pathspecs)
    try:
        tracked = subprocess.run(
            ["git", "diff", "--binary", "--no-ext-diff", "HEAD", "--", *pathspecs],
            cwd=ROOT,
            capture_output=True,
            check=True,
        ).stdout
        untracked_result = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard", "-z", "--", *pathspecs],
            cwd=ROOT,
            capture_output=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise SourceSnapshotError(f"git_runtime_source_query_failed: {error}") from error
    untracked: dict[str, bytes] = {}
    for raw in sorted(p for p in untracked_result.split(b"\0") if p):
        relative_path = raw.decode("utf-8", errors="surrogateescape")
        try:
            untracked[relative_path] = (ROOT / relative_path).read_bytes()
        except FileNotFoundError:
            continue  # listed, then deleted before the read: not executed
        except OSError as error:
            raise SourceSnapshotError(f"untracked_runtime_file_unreadable: {relative_path}: {error}") from error
    return {"head": _git_head(), "tracked_diff": tracked, "untracked": untracked, "pathspecs": pathspecs}


def _runtime_source_digest(
    tracked_diff: bytes,
    untracked: dict[str, bytes],
    pathspecs: tuple[str, ...] | None = None,
) -> str:
    """sha256 over the tracked diff and every untracked path plus bytes.

    Same encoding as the pre-FL-4888 digest, so older receipts that carry only
    a digest remain comparable. A non-default pathspec tuple is hashed first:
    two scopes whose dirty bytes happen to agree must still get two content
    addresses, because their snapshot.json manifests differ.
    """
    digest = hashlib.sha256()
    if pathspecs is not None and tuple(pathspecs) != RUNTIME_SOURCE_PATHSPECS:
        digest.update(b"pathspecs\0")
        digest.update("\0".join(pathspecs).encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
    digest.update(b"tracked-diff\0")
    digest.update(tracked_diff)
    for relative_path in sorted(untracked):
        digest.update(b"untracked-path\0")
        digest.update(relative_path.encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        digest.update(untracked[relative_path])
    return digest.hexdigest()


SNAPSHOT_ADDRESS_SCHEMA = b"asciicker.runtime_source_snapshot_address.v2"
SOURCE_SNAPSHOT_SHARD_BYTES = 90_000_000


def _snapshot_address(
    head: str, pathspecs: tuple[str, ...], tracked_diff: bytes, untracked: dict[str, bytes]
) -> str:
    """Content address of a runtime source snapshot (FL-4888 review P3).

    sha256 over EVERYTHING the snapshot names: the head commit, the pathspec
    set, the tracked diff bytes and every untracked path with its bytes, each
    length-prefixed. The v1 digest (_runtime_source_digest) omitted the head,
    so one dirty diff on two different heads shared a directory. Two different
    trees can no longer share an address; one tree always gets the same one.
    """
    digest = hashlib.sha256()

    def field(tag: bytes, value: bytes) -> None:
        digest.update(tag + b"\0" + str(len(value)).encode("ascii") + b"\0" + value)

    field(b"schema", SNAPSHOT_ADDRESS_SCHEMA)
    field(b"head", head.encode("utf-8"))
    field(b"pathspecs", "\0".join(pathspecs).encode("utf-8", errors="surrogateescape"))
    field(b"tracked-diff", tracked_diff)
    for relative_path in sorted(untracked):
        field(b"untracked-path", relative_path.encode("utf-8", errors="surrogateescape"))
        field(b"untracked-bytes", untracked[relative_path])
    return digest.hexdigest()


def _repo_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _write_immutable(path: Path, data: bytes) -> None:
    """Write *data* once. Identical bytes are a no-op; different bytes refuse."""
    if path.exists() or path.is_symlink():
        if not path.is_file() or path.read_bytes() != data:
            raise SourceSnapshotError(f"refusing_to_overwrite_preserved_evidence: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _tracked_diff_bytes(manifest: dict, snapshot_dir: Path) -> bytes:
    """Read and verify a legacy single-file or ordinary-Git sharded diff."""
    recorded = manifest.get("tracked_diff") or {}
    parts = recorded.get("parts")
    if parts is None:
        name = str(recorded.get("file") or "tracked.diff")
        rows = [{"file": name, "bytes": recorded.get("bytes"),
                 "sha256": recorded.get("sha256")}]
    elif not isinstance(parts, list) or not parts:
        raise SourceSnapshotError("snapshot_tracked_diff_parts_invalid")
    else:
        rows = parts
    chunks: list[bytes] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or not isinstance(row.get("file"), str):
            raise SourceSnapshotError(f"snapshot_tracked_diff_part_invalid: {index}")
        path = snapshot_dir / row["file"]
        try:
            data = path.read_bytes()
        except OSError as error:
            raise SourceSnapshotError(f"snapshot_tracked_diff_missing: {path}: {error}") from error
        if len(data) != row.get("bytes") or hashlib.sha256(data).hexdigest() != row.get("sha256"):
            raise SourceSnapshotError(f"snapshot_tracked_diff_part_tampered: {path}")
        chunks.append(data)
    data = b"".join(chunks)
    if len(data) != recorded.get("bytes") or hashlib.sha256(data).hexdigest() != recorded.get("sha256"):
        raise SourceSnapshotError("snapshot_tracked_diff_tampered")
    return data


def _preserve_tracked_diff(snapshot_dir: Path, data: bytes) -> dict:
    """Write a transport-safe diff representation and return its manifest row."""
    manifest_path = snapshot_dir / "snapshot.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise SourceSnapshotError(f"snapshot_manifest_unreadable: {manifest_path}: {error}") from error
        try:
            preserved = _tracked_diff_bytes(manifest, snapshot_dir)
        except SourceSnapshotError as error:
            raise SourceSnapshotError(
                f"refusing_to_overwrite_preserved_evidence: {snapshot_dir}: {error}"
            ) from error
        if preserved != data:
            raise SourceSnapshotError(f"refusing_to_overwrite_preserved_evidence: {snapshot_dir}")
        return dict(manifest["tracked_diff"])
    digest = hashlib.sha256(data).hexdigest()
    if len(data) <= SOURCE_SNAPSHOT_SHARD_BYTES:
        _write_immutable(snapshot_dir / "tracked.diff", data)
        return {"file": "tracked.diff", "bytes": len(data), "sha256": digest}
    parts = []
    for index, start in enumerate(range(0, len(data), SOURCE_SNAPSHOT_SHARD_BYTES)):
        chunk = data[start:start + SOURCE_SNAPSHOT_SHARD_BYTES]
        name = f"tracked.diff.part-{index:03d}"
        _write_immutable(snapshot_dir / name, chunk)
        parts.append({"file": name, "bytes": len(chunk),
                      "sha256": hashlib.sha256(chunk).hexdigest()})
    return {"bytes": len(data), "sha256": digest, "parts": parts}


def _preserve_source_snapshot(capture: dict[str, object], sha256: str, snapshot_root: Path) -> Path:
    """Store the captured source under snapshot_root/<sha256>/ and return snapshot.json.

    Layout: a tracked diff (one tracked.diff or ordered transport-safe shards),
    untracked/<path> (verbatim bytes), and snapshot.json (head, pathspecs,
    per-file hashes, rebuild recipe). The preserved bytes are re-hashed before
    the snapshot is accepted, so the directory name is proven, not assumed.
    """
    tracked_diff = capture["tracked_diff"]
    untracked = capture["untracked"]
    assert isinstance(tracked_diff, bytes) and isinstance(untracked, dict)
    pathspecs = tuple(capture.get("pathspecs") or RUNTIME_SOURCE_PATHSPECS)
    snapshot_dir = snapshot_root / sha256
    tracked_diff_row = _preserve_tracked_diff(snapshot_dir, tracked_diff)
    rows = []
    for relative_path in sorted(untracked):
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise SourceSnapshotError(f"unsafe_untracked_path: {relative_path}")
        data = untracked[relative_path]
        _write_immutable(snapshot_dir / "untracked" / relative, data)
        rows.append({"path": relative_path, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    preserved_untracked = {
        row["path"]: (snapshot_dir / "untracked" / row["path"]).read_bytes() for row in rows
    }
    preserved_digest = _snapshot_address(
        str(capture["head"]), pathspecs, tracked_diff, preserved_untracked
    )
    if preserved_digest != sha256:
        raise SourceSnapshotError(f"preserved_snapshot_digest_mismatch: {preserved_digest} != {sha256}")
    manifest = {
        "schema": SOURCE_SNAPSHOT_SCHEMA,
        "runtime_source_sha256": sha256,
        "address": "sha256 over schema, head, pathspecs, tracked diff, untracked paths and bytes "
                   "(launch_fl4359_manual_play._snapshot_address)",
        "content_sha256_v1": _runtime_source_digest(tracked_diff, untracked, pathspecs),
        "head": capture["head"],
        "pathspecs": list(pathspecs),
        "tracked_diff": tracked_diff_row,
        "untracked": rows,
        "rebuild": [
            "git checkout <head>",
            "concatenate tracked_diff.parts in order when present; otherwise use tracked_diff.file",
            "git apply --binary <reconstructed tracked diff>  (skip when bytes == 0)",
            "copy untracked/<path> to <path>",
        ],
    }
    manifest_path = snapshot_dir / "snapshot.json"
    _write_immutable(manifest_path, (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return manifest_path


def _runtime_source_state(
    snapshot_root: Path, pathspecs: tuple[str, ...] = RUNTIME_SOURCE_PATHSPECS
) -> dict[str, object]:
    """Identify AND preserve the exact runtime source used by a manual route.

    HEAD alone is ambiguous in this repository because runs exercise
    uncommitted shader and script edits. The digest covers tracked changes plus
    every untracked file under RUNTIME_SOURCE_PATHSPECS, and the same bytes are
    written content-addressed under snapshot_root, so '<head>-dirty' names a
    tree that still exists. Raises SourceSnapshotError when the source cannot
    be identified or preserved; the caller treats that as a launch failure.
    """
    capture = _capture_runtime_source(pathspecs)
    tracked_diff = capture["tracked_diff"]
    untracked = capture["untracked"]
    assert isinstance(tracked_diff, bytes) and isinstance(untracked, dict)
    sha256 = _snapshot_address(str(capture["head"]), tuple(pathspecs), tracked_diff, untracked)
    snapshot_path = _preserve_source_snapshot(capture, sha256, snapshot_root)
    return {
        "available": True,
        "head": capture["head"],
        "dirty": bool(tracked_diff or untracked),
        "sha256": sha256,
        # The pre-review digest (no head), kept only to compare older receipts.
        "content_sha256_v1": _runtime_source_digest(tracked_diff, untracked, tuple(pathspecs)),
        "untracked_paths": sorted(untracked),
        "snapshot": _repo_relative(snapshot_path),
    }


def snapshot_file_bytes(snapshot_manifest: Path, paths) -> dict[str, bytes | None]:
    """Bytes of each repo-relative path in the tree a preserved snapshot names.

    Reads the snapshot's head through a TEMPORARY index (GIT_INDEX_FILE),
    applies the verified tracked diff to that index only (git apply --cached
    --binary) and
    overlays untracked/<path>. No checkout and no worktree: the working tree
    and the real index are never touched; git apply --cached only adds blob
    objects to the object store. None means the path is absent from the
    snapshot tree. Raises SourceSnapshotError when the snapshot is incomplete,
    tampered, or git cannot answer.
    """
    try:
        manifest = json.loads(Path(snapshot_manifest).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SourceSnapshotError(f"snapshot_manifest_unreadable: {snapshot_manifest}: {error}") from error
    snapshot_dir = Path(snapshot_manifest).parent
    head = str(manifest.get("head", ""))
    if not head:
        raise SourceSnapshotError(f"snapshot_head_missing: {snapshot_manifest}")
    wanted = sorted({str(p) for p in paths})
    result: dict[str, bytes | None] = {p: None for p in wanted}
    untracked_rows = {str(row["path"]): row for row in manifest.get("untracked", [])}
    for path in wanted:
        row = untracked_rows.get(path)
        if row is None:
            continue
        try:
            data = (snapshot_dir / "untracked" / path).read_bytes()
        except OSError as error:
            raise SourceSnapshotError(f"snapshot_untracked_missing: {path}: {error}") from error
        if hashlib.sha256(data).hexdigest() != row.get("sha256"):
            raise SourceSnapshotError(f"snapshot_untracked_tampered: {path}")
        result[path] = data
    tracked_wanted = [p for p in wanted if p not in untracked_rows]
    if not tracked_wanted:
        return result
    diff_bytes = _tracked_diff_bytes(manifest, snapshot_dir)
    import tempfile

    with tempfile.TemporaryDirectory(prefix="snapshot-index-") as temporary:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(temporary) / "index"))
        git = ["git", "--literal-pathspecs"]
        try:
            subprocess.run([*git, "read-tree", head], cwd=ROOT, env=env, capture_output=True, check=True)
            if diff_bytes:
                subprocess.run(
                    [*git, "apply", "--cached", "--binary", "-"],
                    cwd=ROOT, env=env, input=diff_bytes, capture_output=True, check=True,
                )
            listed = subprocess.run(
                [*git, "ls-files", "-s", "-z", "--", *tracked_wanted],
                cwd=ROOT, env=env, capture_output=True, check=True,
            ).stdout
        except (OSError, subprocess.CalledProcessError) as error:
            detail = getattr(error, "stderr", b"") or b""
            raise SourceSnapshotError(
                f"snapshot_rebuild_failed: {error}: {detail.decode('utf-8', 'replace')[-400:]}"
            ) from error
    blobs: dict[str, str] = {}
    for entry in listed.split(b"\0"):
        if not entry:
            continue
        meta, _, raw_path = entry.partition(b"\t")
        fields = meta.split()
        if len(fields) >= 2:
            blobs[raw_path.decode("utf-8", errors="surrogateescape")] = fields[1].decode("ascii")
    if blobs:
        order = [p for p in tracked_wanted if p in blobs]
        try:
            batch = subprocess.run(
                ["git", "cat-file", "--batch"],
                cwd=ROOT,
                input=b"".join(blobs[p].encode("ascii") + b"\n" for p in order),
                capture_output=True,
                check=True,
            ).stdout
        except (OSError, subprocess.CalledProcessError) as error:
            raise SourceSnapshotError(f"snapshot_blob_read_failed: {error}") from error
        offset = 0
        for path in order:
            newline = batch.index(b"\n", offset)
            header = batch[offset:newline].split()
            if len(header) != 3 or header[1] != b"blob":
                raise SourceSnapshotError(f"snapshot_blob_missing: {path}")
            size = int(header[2])
            start = newline + 1
            result[path] = batch[start:start + size]
            offset = start + size + 1
    return result


# FL-4873 snapshot materialization. Generated files are judged by the
# snapshot's own .gitignore rules (verify_materialized_tree), not a fixed list.
GITLINK_MODE = "160000"
DEPENDENCY_DIR_NAME = "node_modules"
DEPENDENCY_LOCKFILES = ("package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml")
MATERIALIZE_IMPORT_TIMEOUT_SECONDS = 900
GENERATED_IGNORED_REPORT_LIMIT = 200
MATERIALIZE_RECEIPT_SCHEMA = "asciicker.materialized_snapshot_receipt.v1"


def _load_snapshot_manifest(snapshot_manifest: Path) -> dict:
    try:
        return json.loads(Path(snapshot_manifest).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SourceSnapshotError(f"snapshot_manifest_unreadable: {snapshot_manifest}: {error}") from error


def verify_snapshot_address(snapshot_manifest: Path) -> str:
    """Re-derive the snapshot address from its preserved bytes; raise on mismatch."""
    manifest = _load_snapshot_manifest(snapshot_manifest)
    snapshot_dir = Path(snapshot_manifest).parent
    untracked = {}
    for row in manifest.get("untracked", []):
        data = (snapshot_dir / "untracked" / row["path"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise SourceSnapshotError(f"snapshot_untracked_tampered: {row['path']}")
        untracked[row["path"]] = data
    address = _snapshot_address(str(manifest.get("head", "")), tuple(manifest.get("pathspecs") or ()),
                                _tracked_diff_bytes(manifest, snapshot_dir), untracked)
    if address != manifest.get("runtime_source_sha256") or address != snapshot_dir.name:
        raise SourceSnapshotError(
            f"snapshot_address_mismatch: bytes give {address}, recorded {manifest.get('runtime_source_sha256')}, "
            f"directory {snapshot_dir.name}")
    return address


def _git_blob_id(data: bytes, object_format: str) -> str:
    header = b"blob " + str(len(data)).encode("ascii") + b"\0"
    return (hashlib.sha256 if object_format == "sha256" else hashlib.sha1)(header + data).hexdigest()


def _expected_snapshot_listing(manifest: dict, snapshot_dir: Path) -> tuple[dict[str, tuple[str, str]], str]:
    """{path: (mode, object id)} of the snapshot tree under its pathspecs, from a
    temporary index only (no checkout, no worktree).

    Gitlink entries (mode 160000, a submodule commit such as
    godot_project/extensions/terrain_3d_a3d/upstream/godot-cpp) are kept with
    their mode: git archive writes them as an empty directory, so the verifier
    expects a directory there, never a file."""
    import tempfile

    head = str(manifest["head"])
    pathspecs = list(manifest.get("pathspecs") or ())
    object_format = subprocess.run(["git", "rev-parse", "--show-object-format"], cwd=ROOT,
                                   capture_output=True, text=True).stdout.strip() or "sha1"
    with tempfile.TemporaryDirectory(prefix="snapshot-listing-") as temporary:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(temporary) / "index"))
        git = ["git", "--literal-pathspecs"]
        try:
            subprocess.run([*git, "read-tree", head], cwd=ROOT, env=env, capture_output=True, check=True)
            diff_bytes = _tracked_diff_bytes(manifest, snapshot_dir)
            if diff_bytes:
                subprocess.run([*git, "apply", "--cached", "--binary", "-"],
                               cwd=ROOT, env=env, input=diff_bytes, capture_output=True, check=True)
            listed = subprocess.run([*git, "ls-files", "-s", "-z", "--", *pathspecs],
                                    cwd=ROOT, env=env, capture_output=True, check=True).stdout
        except (OSError, subprocess.CalledProcessError) as error:
            raise SourceSnapshotError(f"snapshot_listing_failed: {error}") from error
    listing: dict[str, tuple[str, str]] = {}
    for entry in listed.split(b"\0"):
        if entry:
            meta, _, raw_path = entry.partition(b"\t")
            mode, oid = meta.split()[:2]
            listing[raw_path.decode("utf-8", errors="surrogateescape")] = (mode.decode(), oid.decode())
    for row in manifest.get("untracked", []):
        data = (snapshot_dir / "untracked" / row["path"]).read_bytes()
        listing[row["path"]] = ("100644", _git_blob_id(data, object_format))
    return listing, object_format


def _snapshot_listed_bytes(manifest: dict, snapshot_dir: Path, listing: dict[str, tuple[str, str]],
                           paths) -> dict[str, bytes]:
    """Bytes of listed snapshot paths: untracked/<path> for untracked rows, the
    listed blob id from the object store otherwise (git apply --cached wrote it)."""
    untracked = {str(row["path"]) for row in manifest.get("untracked", [])}
    result: dict[str, bytes] = {}
    for path in paths:
        if path in untracked:
            result[path] = (snapshot_dir / "untracked" / path).read_bytes()
            continue
        try:
            result[path] = subprocess.run(["git", "cat-file", "blob", listing[path][1]], cwd=ROOT,
                                          capture_output=True, check=True).stdout
        except (OSError, subprocess.CalledProcessError) as error:
            raise SourceSnapshotError(f"snapshot_blob_missing: {path}: {error}") from error
    return result


def _snapshot_ignore_rules(manifest: dict, snapshot_dir: Path,
                           listing: dict[str, tuple[str, str]]) -> dict[str, bytes]:
    """{path of a .gitignore: bytes} of every rule file that governs the
    snapshot's pathspecs, taken from the SNAPSHOT (never from the checkout or
    the materialized tree): each .gitignore inside the pathspecs (listing), plus
    each .gitignore in an ancestor directory of a pathspec (the repository root
    .gitignore, scripts/.gitignore, ...), read from the snapshot head. Ancestors
    are outside the pathspecs, so tracked.diff cannot change them."""
    rules = _snapshot_listed_bytes(manifest, snapshot_dir, listing, sorted(
        path for path, (mode, _) in listing.items()
        if Path(path).name == ".gitignore" and mode != GITLINK_MODE))
    head = str(manifest["head"])
    ancestors: set[str] = set()
    for spec in manifest.get("pathspecs") or ():
        parts = Path(spec).parts
        for depth in range(len(parts)):
            ancestors.add("/".join(parts[:depth] + (".gitignore",)))
    for candidate in sorted(ancestors - set(rules)):
        shown = subprocess.run(["git", "cat-file", "blob", f"{head}:{candidate}"], cwd=ROOT, capture_output=True)
        if shown.returncode == 0:
            rules[candidate] = shown.stdout
    return rules


def _snapshot_ignored(rules: dict[str, bytes], paths: list[str]) -> set[str]:
    """Paths the snapshot's own .gitignore rules ignore.

    Method (FL-4873): the rule files are written into a scratch directory with
    its own `git init` (a throwaway repository, never the checkout and never
    the materialized tree), and `git check-ignore --no-index --stdin -z` runs
    there with global/system config and the XDG excludes file disabled, so
    only the snapshot's rules decide. Git itself evaluates the rules
    (negation, directory patterns, `**`, per-directory scope); the queried
    paths need not exist in the scratch directory."""
    import tempfile

    if not paths or not rules:
        return set()
    with tempfile.TemporaryDirectory(prefix="snapshot-ignore-") as temporary:
        scratch = Path(temporary)
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("GIT_") or key == "GIT_EXEC_PATH"}
        env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", HOME=temporary,
                   XDG_CONFIG_HOME=temporary, GIT_CEILING_DIRECTORIES=str(scratch.parent))
        try:
            subprocess.run(["git", "init", "-q", temporary], env=env, capture_output=True, check=True)
            for path, data in rules.items():
                target = scratch / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            checked = subprocess.run(
                ["git", "check-ignore", "--no-index", "--stdin", "-z"], cwd=temporary, env=env,
                input=b"".join(p.encode("utf-8", errors="surrogateescape") + b"\0" for p in paths),
                capture_output=True)
        except (OSError, subprocess.CalledProcessError) as error:
            raise SourceSnapshotError(f"snapshot_ignore_rules_failed: {error}") from error
    if checked.returncode not in (0, 1):
        raise SourceSnapshotError(
            f"snapshot_ignore_rules_failed: rc={checked.returncode}: {checked.stderr.decode('utf-8', 'replace')[-400:]}")
    return {p.decode("utf-8", errors="surrogateescape") for p in checked.stdout.split(b"\0") if p}


def _file_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _dependency_packages(listing: dict[str, tuple[str, str]]) -> list[tuple[str, str]]:
    """[(package dir, lockfile path)] for every snapshot directory holding a
    package.json and a lockfile; each gets <dir>/node_modules provisioned."""
    packages = []
    for path in sorted(listing):
        if Path(path).name != "package.json":
            continue
        directory = Path(path).parent.as_posix()
        prefix = "" if directory == "." else directory + "/"
        lock = next((prefix + name for name in DEPENDENCY_LOCKFILES if prefix + name in listing), None)
        if lock is not None:
            packages.append(("" if directory == "." else directory, lock))
    return packages


def _dependency_identity(manifest: dict, snapshot_dir: Path, listing: dict[str, tuple[str, str]],
                         directory: str, lock: str, package_root: Path) -> dict:
    """sha256 of the snapshot's package.json and lockfile against the same-named
    files in package_root (the directory that holds the node_modules used):
    equal bytes mean those modules were installed for exactly this dependency set."""
    prefix = directory + "/" if directory else ""
    names = [prefix + "package.json", lock]
    snapshot_bytes = _snapshot_listed_bytes(manifest, snapshot_dir, listing, names)
    rows = {}
    for name in names:
        rows[name] = {"snapshot_sha256": hashlib.sha256(snapshot_bytes[name]).hexdigest(),
                      "checkout_sha256": _file_sha256(package_root / Path(name).name)}
    return {"package_dir": directory or ".", "files": rows,
            "equal": all(r["snapshot_sha256"] == r["checkout_sha256"] for r in rows.values())}


def verify_materialized_tree(snapshot_manifest: Path, root: Path) -> dict:
    """Does the tree at root hold exactly the snapshot's files under its pathspecs?

    Compares every path's blob id (and, for tracked files, its exec/symlink
    mode) with the snapshot tree (head + tracked.diff + untracked). A gitlink
    (mode 160000) must be a directory. An extra file that the SNAPSHOT's own
    .gitignore rules ignore (the Godot import cache, *.glsl.import sidecars,
    __pycache__, .DS_Store) is reported in generated_ignored, not as a
    mismatch; an extra <file>.uid whose <file> is a regular snapshot file and
    which the snapshot does not list is a Godot-generated UID sidecar, reported
    in generated_uid_sidecars (path + uid), not a mismatch; any other extra
    file is a mismatch. <pkg>/node_modules, where
    the snapshot holds <pkg>/package.json and a lockfile, must be the symlink
    materialize_snapshot provisions, pointing at a node_modules whose sibling
    package.json and lockfile equal the snapshot's bytes; it is reported in
    provisioned_dependencies and never walked. Also re-derives the address.
    """
    from collections import Counter

    manifest = _load_snapshot_manifest(snapshot_manifest)
    snapshot_dir = Path(snapshot_manifest).parent
    address = verify_snapshot_address(snapshot_manifest)
    expected, object_format = _expected_snapshot_listing(manifest, snapshot_dir)
    root = Path(root)
    untracked = {r["path"] for r in manifest.get("untracked", [])}
    missing, differs, extra, gitlinks = [], [], [], []
    for path, (mode, oid) in sorted(expected.items()):
        target = root / path
        if mode == GITLINK_MODE:
            gitlinks.append({"path": path, "commit": oid, "present": target.is_dir() and not target.is_symlink()})
            if not gitlinks[-1]["present"]:
                missing.append(path)
            continue
        if target.is_symlink():
            actual_mode, data = "120000", os.readlink(target).encode("utf-8", errors="surrogateescape")
        elif target.is_file():
            actual_mode = "100755" if os.access(target, os.X_OK) else "100644"
            data = target.read_bytes()
        else:
            missing.append(path)
            continue
        if _git_blob_id(data, object_format) != oid or (mode != actual_mode and path not in untracked):
            differs.append(path)
    provisioned, dependency_problems = [], []
    skip = set()
    for directory, lock in _dependency_packages(expected):
        link_path = (directory + "/" if directory else "") + DEPENDENCY_DIR_NAME
        link = root / link_path
        skip.add(link_path)
        if not link.is_symlink():
            dependency_problems.append({"path": link_path, "reason": "dependencies_not_provisioned"})
            continue
        target = Path(os.readlink(link))
        target = target if target.is_absolute() else (link.parent / target)
        target = target.resolve()
        identity = _dependency_identity(manifest, snapshot_dir, expected, directory, lock, target.parent)
        identity = dict(identity, path=link_path, target=str(target), target_is_dir=target.is_dir())
        provisioned.append(identity)
        if not (identity["equal"] and identity["target_is_dir"]):
            dependency_problems.append({"path": link_path, "reason": "dependency_identity_mismatch"})
    candidates = []
    expected_dirs = {path for path, (mode, _) in expected.items() if mode == GITLINK_MODE}
    for spec in manifest.get("pathspecs") or ():
        base = root / spec
        if base.is_file() or base.is_symlink():
            candidates.append(spec)
            continue
        for current, dirnames, filenames in os.walk(base):
            relative_dir = Path(current).relative_to(root).as_posix()
            kept = []
            for name in dirnames:
                relative = f"{relative_dir}/{name}"
                if relative in skip:
                    continue
                if os.path.islink(os.path.join(current, name)):
                    candidates.append(relative)  # a symlinked directory is a file entry to git
                else:
                    kept.append(name)
            dirnames[:] = kept
            candidates.extend(f"{relative_dir}/{name}" for name in filenames)
    unexpected = sorted(p for p in candidates if p not in expected and p not in expected_dirs)
    ignored = _snapshot_ignored(_snapshot_ignore_rules(manifest, snapshot_dir, expected), unexpected)
    generated_ignored = [p for p in unexpected if p in ignored]
    # Godot UID sidecars (FL-4873 decision): `godot --import` writes <file>.uid
    # for a snapshot script that has none. Named and reported, never ignored:
    # the sidecar's snapshot sibling must be a regular listed file and the
    # sidecar itself must be absent from the snapshot. A listed .uid that
    # differs is in `differs`; a .uid with no listed sibling stays `extra`.
    uid_sidecars = []
    for p in unexpected:
        if p in ignored or not p.endswith(".uid"):
            continue
        sibling = expected.get(p[:-4])
        if sibling is not None and sibling[0] in ("100644", "100755") and (root / p).is_file() \
                and not (root / p).is_symlink():
            uid_sidecars.append({"path": p, "uid": (root / p).read_text(encoding="utf-8", errors="replace").strip()})
    sidecar_paths = {row["path"] for row in uid_sidecars}
    extra = [p for p in unexpected if p not in ignored and p not in sidecar_paths]
    status = "verified" if not (missing or differs or extra or dependency_problems) else "mismatch"
    return {"status": status, "address": address, "files": len(expected), "missing": missing[:50],
            "differs": differs[:50], "extra": extra[:50], "extra_count": len(extra),
            "gitlinks": gitlinks, "provisioned_dependencies": provisioned,
            "generated_uid_sidecars": uid_sidecars,
            "dependency_problems": dependency_problems,
            "generated_ignored_count": len(generated_ignored),
            "generated_ignored_by_dir": dict(sorted(Counter(
                Path(p).parent.as_posix() for p in generated_ignored).items(), key=lambda kv: -kv[1])[:30]),
            "generated_ignored": generated_ignored[:GENERATED_IGNORED_REPORT_LIMIT],
            "ignore_method": "snapshot .gitignore rules via git check-ignore --no-index in a scratch repository"}


def _provision_dependencies(manifest: dict, snapshot_dir: Path, dest: Path) -> list[dict]:
    """Symlink the checkout's <pkg>/node_modules into dest/<pkg> for every
    snapshot package whose package.json and lockfile bytes equal the
    checkout's. Refuses (dependency_identity_mismatch / dependency_source_missing)
    otherwise: installed modules for another lockfile are not this snapshot's."""
    listing, _ = _expected_snapshot_listing(manifest, snapshot_dir)
    rows = []
    for directory, lock in _dependency_packages(listing):
        identity = _dependency_identity(manifest, snapshot_dir, listing, directory, lock, ROOT / directory)
        if not identity["equal"]:
            raise SourceSnapshotError(f"dependency_identity_mismatch: {json.dumps(identity, sort_keys=True)}")
        source = (ROOT / directory / DEPENDENCY_DIR_NAME).resolve()
        if not source.is_dir():
            raise SourceSnapshotError(f"dependency_source_missing: {source}")
        link = dest / directory / DEPENDENCY_DIR_NAME
        link.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(source, link, target_is_directory=True)
        rows.append(dict(identity, path=link.relative_to(dest).as_posix(), target=str(source),
                         method="symlink to the checkout's installed dependencies"))
    return rows


def _import_godot_project(project: Path, timeout: float) -> dict:
    """`<GODOT_BIN> --headless --path <project> --import` on the materialized
    project so its .godot import cache exists before any run (bounded)."""
    if not (project / "project.godot").is_file():
        return {"status": "skipped", "reason": "no_project_godot", "project": str(project)}
    command = [GODOT_BIN, "--headless", "--path", str(project), "--import"]
    started = time.monotonic()
    try:
        proc = subprocess.run(command, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise SourceSnapshotError(f"materialize_import_timeout: {timeout}s: {command}") from error
    except OSError as error:
        raise SourceSnapshotError(f"materialize_import_failed: {command}: {error}") from error
    duration = round(time.monotonic() - started, 3)
    output = (proc.stdout + proc.stderr).decode("utf-8", "replace")
    result = {"status": "imported" if proc.returncode == 0 else "failed", "command": command,
              "rc": proc.returncode, "duration_seconds": duration, "timeout_seconds": timeout,
              "output_tail": output[-2000:]}
    if proc.returncode != 0:
        raise SourceSnapshotError(f"materialize_import_failed: {json.dumps(result)[-1200:]}")
    return result


def materialize_snapshot(snapshot_manifest: Path, dest: Path,
                         import_timeout: float = MATERIALIZE_IMPORT_TIMEOUT_SECONDS) -> dict:
    """Write the snapshot's tree into dest (outside the repository) and verify it.

    No checkout and no git worktree: `git archive <head> -- <pathspecs>` is
    extracted into dest, tracked.diff is applied there by `git apply` running
    outside any repository (GIT_CEILING_DIRECTORIES stops discovery), and the
    untracked bytes are copied. Then: dependencies are provisioned
    (_provision_dependencies), the Godot project is imported headless
    (_import_godot_project), and the tree is verified. The materialize
    receipt is written content-addressed to
    <snapshot dir>/materializations/<sha256>.json before this returns.
    Raises SourceSnapshotError when dest is inside the repository, is not
    empty, dependencies do not match, the import fails, or the result does
    not verify.
    """
    import io
    import tarfile

    manifest = _load_snapshot_manifest(snapshot_manifest)
    snapshot_dir = Path(snapshot_manifest).parent
    verify_snapshot_address(snapshot_manifest)
    dest = Path(dest).expanduser().resolve()
    try:
        dest.relative_to(ROOT.resolve())
        raise SourceSnapshotError(f"materialize_dir_inside_repository: {dest}")
    except ValueError:
        pass
    if dest.exists() and any(dest.iterdir()):
        raise SourceSnapshotError(f"materialize_dir_not_empty: {dest}")
    dest.mkdir(parents=True, exist_ok=True)
    head = str(manifest["head"])
    present = [spec for spec in manifest.get("pathspecs") or ()
               if subprocess.run(["git", "--literal-pathspecs", "ls-tree", "--name-only", head, "--", spec],
                                 cwd=ROOT, capture_output=True).stdout.strip()]
    try:
        if present:
            archive = subprocess.run(["git", "--literal-pathspecs", "archive", "--format=tar", head, "--", *present],
                                     cwd=ROOT, capture_output=True, check=True).stdout
            with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
                tar.extractall(dest, filter="data")
        diff_bytes = _tracked_diff_bytes(manifest, snapshot_dir)
        if diff_bytes:
            env = dict(os.environ, GIT_CEILING_DIRECTORIES=str(dest.parent))
            env.pop("GIT_DIR", None)
            env.pop("GIT_WORK_TREE", None)
            subprocess.run(["git", "apply", "--binary", "--whitespace=nowarn", "-"],
                           cwd=dest, env=env, input=diff_bytes, capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError, tarfile.TarError) as error:
        detail = getattr(error, "stderr", b"") or b""
        raise SourceSnapshotError(f"materialize_failed: {error}: {detail.decode('utf-8', 'replace')[-400:]}") from error
    for row in manifest.get("untracked", []):
        target = dest / row["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((snapshot_dir / "untracked" / row["path"]).read_bytes())
    receipt: dict = {"schema": MATERIALIZE_RECEIPT_SCHEMA, "root": str(dest), "head": head,
                     "snapshot": _repo_relative(Path(snapshot_manifest)), "status": "failed"}
    failure: SourceSnapshotError | None = None
    try:
        receipt["dependencies"] = _provision_dependencies(manifest, snapshot_dir, dest)
        receipt["godot_import"] = _import_godot_project(dest / "godot_project", import_timeout)
        receipt.update(verify_materialized_tree(snapshot_manifest, dest))
    except SourceSnapshotError as error:
        failure = error
        receipt["error"] = str(error)
    # The receipt is preserved on success AND failure, before anything is reported.
    data = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8")
    receipt_path = snapshot_dir / "materializations" / f"{hashlib.sha256(data).hexdigest()}.json"
    _write_immutable(receipt_path, data)
    print(f"[materialize] receipt: {_repo_relative(receipt_path)}", flush=True)
    if failure is not None:
        raise SourceSnapshotError(f"{failure} (receipt {_repo_relative(receipt_path)})") from failure
    if receipt["status"] != "verified":
        summary = {key: receipt[key] for key in ("missing", "differs", "extra", "dependency_problems")}
        raise SourceSnapshotError(f"materialized_tree_mismatch: {_repo_relative(receipt_path)}: {json.dumps(summary)}")
    return dict(receipt, materialize_receipt=_repo_relative(receipt_path))


def _run_identity(source_state: dict[str, object], route_receipt_path: Path) -> dict[str, object]:
    """The one run identity every Godot consumer reads (World, recorder, dumps)."""
    head = str(source_state.get("head", ""))
    sha256 = str(source_state.get("sha256", ""))
    dirty = bool(source_state.get("dirty", True))
    return {
        "schema": RUN_IDENTITY_SCHEMA,
        "owner": "scripts/launch_fl4359_manual_play.py",
        "label": f"{head[:9] or 'nohead'}-{'dirty' if dirty else 'clean'}-{sha256[:12]}",
        "git_head": head,
        "runtime_source_dirty": dirty,
        "runtime_source_sha256": sha256,
        "runtime_source_snapshot": source_state.get("snapshot", ""),
        "runtime_source_pathspecs": list(RUNTIME_SOURCE_PATHSPECS),
        "route_receipt": _repo_relative(route_receipt_path),
    }


def _parse_resolution(value: str) -> tuple[int, int]:
    raw = value.lower().replace("x", " ").replace(",", " ").split()
    if len(raw) != 2:
        raise argparse.ArgumentTypeError("resolution must be WIDTHxHEIGHT")
    width = int(raw[0])
    height = int(raw[1])
    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError("resolution dimensions must be positive")
    return width, height


def _terminate(proc: subprocess.Popen[str], sig: int = signal.SIGINT, timeout: float = 8.0) -> None:
    if proc.poll() is not None:
        return
    proc.send_signal(sig)
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Start the real Colyseus authority plus a human-playable Godot "
            "client for FL-4359 manual perspective review."
        )
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=(
            "Colyseus port. Default: the first free port from 2589. An explicit "
            "--port that is busy is still an error."
        ),
    )
    parser.add_argument("--out", default="")
    parser.add_argument("--server-boot-timeout", type=float, default=30.0)
    parser.add_argument("--resolution", type=_parse_resolution, default=(1280, 800))
    parser.add_argument("--windowed", action="store_true", help="Use a window instead of fullscreen.")
    parser.add_argument("--ortho", action="store_true", help="Start in orthographic mode.")
    parser.add_argument(
        "--perspective",
        action="store_true",
        help="Start in true perspective inspection mode.",
    )
    parser.add_argument(
        "--airborne-airplane",
        action="store_true",
        help="Start the first player mounted in the server-owned airborne airplane.",
    )
    parser.add_argument(
        "--no-window-activation",
        action="store_true",
        help=(
            "Do NOT claim macOS keyboard focus for the Godot window. This is "
            "the control arm for the manual-input focus A/B: with it the "
            "window opens unfocused and keypresses go to the terminal, which "
            "is the defect this launcher previously had unconditionally."
        ),
    )
    parser.add_argument(
        "--actor-reference-marker",
        action="store_true",
        help="Start with the FL-4346 F10 actor-root marker enabled.",
    )
    parser.add_argument(
        "--allow-screen-recording",
        action="store_true",
        help=(
            "Compatibility flag retained for old receipts. Manual F9 screen "
            "recording is togglable in-game and bounded by World's supervisor."
        ),
    )
    parser.add_argument(
        "--map-id",
        type=_map_id_arg,
        default=None,
        help=(
            "FL-4881: runtime map stem from godot_project/assets/maps "
            f"(default {DEFAULT_MAP_ID}). Godot receives --map-id= and joins "
            "the Colyseus room with the same map_id."
        ),
    )
    parser.add_argument(
        "--proof-id",
        default="",
        help=(
            "FL-4895: config/godot_proof_pose_registry.json id of the proof this "
            "session records. Refused when unknown. Forwarded as ASCIICKER_PROOF_ID; "
            "the recorder writes it to identity.proof_id, which titles recording.mov."
        ),
    )
    parser.add_argument("--extra-user-arg", action="append", default=[])
    parser.add_argument(
        "--replicate-reason",
        default="",
        help=(
            "FL-4516: required to relaunch unchanged runtime source after a launch "
            "that did not pass (log rejection, or missing/nonzero RED-YELLOW crawl "
            "proof). Recorded in .run/true_ff_launch_guard/launches.jsonl."
        ),
    )
    args = parser.parse_args()
    if args.proof_id:
        sys.path.insert(0, str(ROOT / "scripts" / "recording"))
        from f9_before_after import registry_ids  # noqa: E402
        if args.proof_id not in registry_ids():
            parser.error(f"--proof-id {args.proof_id!r} is not an id in config/godot_proof_pose_registry.json")

    missing_prerequisites = _product_catalog.godot_missing_prerequisites(ROOT)
    if missing_prerequisites:
        print("[fl4359-manual] ERROR: launch prerequisites are incomplete:", flush=True)
        for missing in missing_prerequisites:
            print(f"  - {missing}", flush=True)
        return 2

    if args.port is None:
        # FL-4881: a stale server left on 2589 by another session made launcher
        # Manual Play exit 2, and the launcher redraw hid the error, so map
        # selection looked like it bounced back to the menu. The default route
        # takes the next free port; client and server both receive it below.
        args.port = next((p for p in range(2589, 2640) if not _port_open(p)), 2589)
        if args.port != 2589:
            print(f"[fl4359-manual] port 2589 busy; using free port {args.port}", flush=True)
    if _port_open(args.port):
        print(
            f"[fl4359-manual] ERROR: port {args.port} is already in use. "
            "Kill stale Colyseus/Godot processes before claiming a manual run.",
            flush=True,
        )
        return 2

    out_dir = Path(args.out) if args.out else ROOT / ".run" / "fl4359_manual_play" / _timestamp()
    if not out_dir.is_absolute():
        out_dir = (ROOT / out_dir).resolve()
    presentation_mode = "FF"
    for extra_arg in args.extra_user_arg:
        if extra_arg.startswith("--presentation-mode="):
            presentation_mode = extra_arg.removeprefix("--presentation-mode=").upper()
    # FL-4516: FF uses the Metal/bank/frame/crawl admission. Explicit FC/CF/CC
    # launches retain the one-use engine token and source revalidation without
    # coupling unrelated gameplay work to True-FF generated products.
    admission = _launch_guard.admit("scripts/launch_fl4359_manual_play.py", out_dir,
                                    args.replicate_reason, project_dir=GODOT_PROJECT,
                                    server_dir=COLYSEUS_DIR,
                                    presentation_mode=presentation_mode)
    if admission != 0:
        return admission
    out_dir.mkdir(parents=True, exist_ok=True)
    server_log = out_dir / "colyseus_server.log"
    godot_log = out_dir / "godot_client.log"
    route_receipt_path = out_dir / "manual_route_receipt.json"
    camera_mode = (
        "orthographic"
        if args.ortho
        else "perspective"
        if args.perspective
        else "native_default"
    )
    launch_scene = _launch_scene_for_user_args(args.extra_user_arg)
    snapshot_root = DURABLE_RECEIPT_ROOT / SOURCE_SNAPSHOT_DIRNAME
    # FL-4888: a run whose source cannot be preserved cannot be traced, so it
    # does not start.
    try:
        start_source_state = _runtime_source_state(snapshot_root)
    except (OSError, SourceSnapshotError) as error:
        print(
            f"[fl4359-manual] ERROR: runtime source snapshot not preserved: {error}",
            flush=True,
        )
        return 7
    run_identity = _run_identity(start_source_state, route_receipt_path)
    print(
        f"[fl4359-manual] run identity {run_identity['label']} "
        f"snapshot={start_source_state['snapshot']}",
        flush=True,
    )
    route_receipt: dict[str, object] = {
        "schema": "fl4359.manual_route_receipt.v1",
        "route_identity": (
            "manual_onfoot_live_colyseus"
            if not args.airborne_airplane
            else "manual_airborne_airplane_live_colyseus"
        ),
        "target": "godot_project",
        "target_path": str(GODOT_PROJECT),
        "build": str(GODOT_APP if sys.platform == "darwin" else GODOT_BIN),
        "room_name": "asciicker_spike",
        "map_id_default": DEFAULT_MAP_ID,
        "map_id_requested": args.map_id,
        "map_id_menu_selection": None,
        "map_id_authoritative": None,
        "scene": launch_scene,
        "camera_mode": camera_mode,
        "mode": camera_mode,
        "input_authority": "real_user_input_only",
        "private_input_mutation": False,
        "driver": None,
        "command": list(sys.argv),
        "extra_user_args": list(args.extra_user_arg),
        "start_head": _git_head(),
        "start_source_state": start_source_state,
        "run_identity": run_identity,
        "start_time": _iso_timestamp(),
        "start_epoch_seconds": time.time(),
        "completion_signal": "launcher_exit_and_godot_client_exit",
        "artifact_paths": {
            "godot_log": str(godot_log),
            "colyseus_log": str(server_log),
            "window_activation": str(out_dir / "window_activation.json"),
        },
    }
    route_receipt_path.write_text(
        json.dumps(route_receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    env = _manual_child_environment(dict(os.environ))
    env["COLYSEUS_PORT"] = str(args.port)
    # FL-4873: executed-source identity observed before Godot starts; every
    # F9 recording of this session is named from it (never a later HEAD).
    sys.path.insert(0, str(Path(__file__).resolve().parent / "recording"))
    import f9_source_identity  # noqa: E402

    launch_identity = f9_source_identity.launch_source_identity("scripts/launch_fl4359_manual_play.py")
    # FL-4888: World, the X dump, the final dump and the F9 manifest read
    # run_identity from this one env door; none of them runs git.
    launch_identity["run_identity"] = run_identity
    env[f9_source_identity.ENV_NAME] = json.dumps(launch_identity, sort_keys=True)
    if args.allow_screen_recording:
        env[SCREEN_RECORDING_ALLOWED_ENV] = "1"
    else:
        env.pop(SCREEN_RECORDING_ALLOWED_ENV, None)
    # FL-4895 MANUAL_F9_PROOF_ID_DEFAULT_20260928: a manual session records as
    # "manual" unless the operator names a registered proof. The recorder
    # writes it to identity.proof_id (the .mov title band then carries mode,
    # projection, cell size, F10 view, git identity and stamp) and the title
    # to the bundle name (FL4359_manual_FF_...). Without it the finalizer
    # refused every manual recording.mov (title_field_missing:proof_id).
    env["ASCIICKER_PROOF_ID"] = str(args.proof_id) if args.proof_id else MANUAL_PROOF_ID
    if not env.get("ASCIICKER_SCREEN_RECORDING_TITLE", "").strip():
        env["ASCIICKER_SCREEN_RECORDING_TITLE"] = str(args.proof_id) if args.proof_id else MANUAL_PROOF_ID
    # FL-4362: the normal manual-play spawn must expose the server-owned boat.
    # Keep the ship at its canonical water position and place the player on the
    # nearby shore; this is gameplay startup state, not a proof pose.
    # FL-4831: race rooms own their map-derived lobby/grid spawn. The boat
    # review default97,0 is far outside the circuit's real grid(-16,49).
    if "--car-race" not in args.extra_user_arg:
        env.setdefault("PLAYER_SPAWN_X", "97.0")
        env.setdefault("PLAYER_SPAWN_Z", "0.0")
    if args.airborne_airplane:
        env["AIRPLANE_SPAWN_AIRBORNE"] = "1"
        env.setdefault("AIRPLANE_SPAWN_ALTITUDE", "45")
        env.setdefault("AIRPLANE_SPAWN_THROTTLE", "0.85")
    else:
        env.pop("AIRPLANE_SPAWN_AIRBORNE", None)
    env.pop("ASCIICKER_ENABLE_DOGFIGHT_BOT", None)

    print(f"[fl4359-manual] starting Colyseus on :{args.port}", flush=True)
    slog = server_log.open("w", encoding="utf-8")
    server = subprocess.Popen(
        ["node", "--import", "tsx", "src/index.ts"],
        cwd=str(COLYSEUS_DIR),
        env=env,
        stdout=slog,
        stderr=subprocess.STDOUT,
        text=True,
    )
    child: subprocess.Popen[str] | _DetachedGodotProcess | None = None

    def listening() -> bool:
        if server.poll() is not None:
            return False
        try:
            return "ASCIICKER_COLYSEUS_SPIKE_LISTENING" in server_log.read_text(encoding="utf-8")
        except OSError:
            return False

    try:
        if not _wait_for(listening, args.server_boot_timeout):
            print("[fl4359-manual] ERROR: Colyseus did not report LISTENING", flush=True)
            if server_log.exists():
                print(server_log.read_text(encoding="utf-8")[-2000:], flush=True)
            return 3

        cmd = [GODOT_BIN]
        if args.windowed:
            cmd.append("--windowed")
        cmd.extend([
            "--resolution",
            f"{args.resolution[0]}x{args.resolution[1]}",
            "--path",
            str(GODOT_PROJECT),
            launch_scene,
            "--",
            "--colyseus-client",
            f"--port={args.port}",
            "--colyseus-skip-room-leave",
        ])
        # FL-4303: World owns durable presentation geometry. The former
        # engine-side --fullscreen request lost to project.godot's delayed
        # 1280x800 override and left manual play visibly windowed.
        if not args.windowed:
            cmd.append("--fl4359-force-fullscreen")
        selected_modes = int(args.ortho) + int(args.perspective)
        if selected_modes > 1:
            print("[fl4359-manual] ERROR: choose one launch view mode", flush=True)
            return 2
        if args.ortho:
            cmd.append("--fl4270-force-ortho")
        elif args.perspective:
            cmd.append("--fl4270-force-perspective")
        if args.actor_reference_marker:
            cmd.append("--fl4346-actor-reference-marker")
        cmd.extend(godot_map_user_args(args.map_id))
        for extra_arg in args.extra_user_arg:
            if extra_arg:
                cmd.append(extra_arg)
        # FL-4516: Main.gd refuses a windowed launch without this admission.
        cmd.append(f"--true-ff-admission={out_dir.resolve() / 'guard_admission.json'}")

        if _launch_guard.before_spawn(out_dir, GODOT_PROJECT, COLYSEUS_DIR) != 0:
            return _launch_guard.REFUSED
        print("[fl4359-manual] launching human-playable Godot client", flush=True)
        print(f"[fl4359-manual] logs={out_dir}", flush=True)
        print("[fl4359-manual] close the Godot window to end the manual session", flush=True)
        launch_receipt: dict[str, object]
        if sys.platform == "darwin":
            child, launch_receipt = _launch_macos_godot_bundle(
                cmd[1:],
                env,
                godot_log,
                GODOT_PROJECT,
                args.port,
                args.no_window_activation,
            )
            if child is None:
                activation = {
                    **launch_receipt,
                    "attempted": False,
                    "activated": False,
                    "reason": str(launch_receipt["launch_reason"]),
                    "frontmost_before": _frontmost_process_name(),
                    "frontmost_after": _frontmost_process_name(),
                }
                (out_dir / "window_activation.json").write_text(
                    json.dumps(activation, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                print(
                    "[fl4359-manual] ERROR: registered Godot engine launch "
                    f"failed: {activation['reason']}",
                    flush=True,
                )
                return 4
        else:
            glog = godot_log.open("w", encoding="utf-8")
            child = subprocess.Popen(
                cmd,
                cwd=str(ROOT),
                env=env,
                stdout=glog,
                stderr=subprocess.STDOUT,
            )
            launch_receipt = {
                "launch_method": "direct executable",
                "bundle_path": "",
                "engine_pid": child.pid,
                "forwarded_environment_keys": sorted(env),
                "launch_reason": "direct_child_started",
            }

        try:
            if args.no_window_activation:
                activation = {
                    **launch_receipt,
                    "attempted": False,
                    "target_pid": child.pid,
                    "target_selector": f"exact engine pid {child.pid}",
                    "activation_method": "explicit background control arm",
                    "activated": False,
                    "reason": "disabled_by_--no-window-activation",
                    "frontmost_before": _frontmost_process_name(),
                    "frontmost_after": "",
                    "activation_command_returncode": None,
                    "activation_command_stderr": "",
                }
            else:
                activation = {
                    **launch_receipt,
                    **_activate_macos_window(child.pid),
                }
            print(
                "[fl4359-manual] window activation: activated=%s reason=%s "
                "frontmost_before=%r frontmost_after=%r"
                % (
                    activation["activated"],
                    activation["reason"],
                    activation["frontmost_before"],
                    activation["frontmost_after"],
                ),
                flush=True,
            )
            if not activation["activated"]:
                if not args.no_window_activation and sys.platform == "darwin":
                    (out_dir / "window_activation.json").write_text(
                        json.dumps(activation, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )
                    print(
                        "[fl4359-manual] ERROR: Godot did not acquire keyboard "
                        "focus; terminating the exact engine instead of leaving "
                        "a misleading manual session open.",
                        flush=True,
                    )
                    return 5
                print(
                    "[fl4359-manual] focus activation disabled by explicit "
                    "control-arm request",
                    flush=True,
                )
            (out_dir / "window_activation.json").write_text(
                json.dumps(activation, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            godot_rc = child.wait()
        finally:
            if sys.platform != "darwin":
                glog.close()
        if isinstance(child, _DetachedGodotProcess):
            print(
                "[fl4359-manual] Godot disappeared; its exit status is unknown "
                f"(launcher rc={godot_rc}) logs={out_dir}",
                flush=True,
            )
        else:
            print(f"[fl4359-manual] Godot exited rc={godot_rc} logs={out_dir}", flush=True)
        return godot_rc
    finally:
        if child is not None:
            _terminate(child)
        _terminate(server)
        slog.close()
        route_receipt["map_id_authoritative"] = _authoritative_map_id_from_server_log(server_log)
        # FL-4881: client and server must agree on the requested map. None
        # means no room was observed (no verdict), never agreement.
        if args.map_id and route_receipt["map_id_authoritative"] is not None:
            route_receipt["map_id_agreement"] = (
                route_receipt["map_id_authoritative"] == args.map_id
            )
            if not route_receipt["map_id_agreement"]:
                print(
                    "[fl4359-manual] ERROR: map mismatch requested="
                    f"{args.map_id} authoritative={route_receipt['map_id_authoritative']}",
                    flush=True,
                )
        else:
            route_receipt["map_id_agreement"] = None
        route_receipt["end_time"] = _iso_timestamp()
        route_receipt["end_epoch_seconds"] = time.time()
        route_receipt["end_head"] = _git_head()
        # Shaders compile at pipeline creation, after launch, so an edit during
        # the run can reach the executed program. The end tree is preserved too.
        try:
            route_receipt["end_source_state"] = _runtime_source_state(snapshot_root)
        except (OSError, SourceSnapshotError) as error:
            route_receipt["end_source_state"] = {"available": False, "reason": str(error)}
        route_receipt["source_state_stable"] = (
            route_receipt["end_source_state"].get("sha256")
            == start_source_state["sha256"]
        )
        detached_exit_unknown = isinstance(child, _DetachedGodotProcess) and (
            "godot_rc" in locals()
        )
        route_receipt["godot_exit_code"] = (
            None if detached_exit_unknown else godot_rc if "godot_rc" in locals() else None
        )
        route_receipt["godot_exit_status"] = (
            {
                "known": False,
                "reason": child.exit_status_reason,
                "launcher_returncode": godot_rc,
            }
            if detached_exit_unknown
            else {
                "known": "godot_rc" in locals(),
                "reason": "direct_child_wait_status" if "godot_rc" in locals() else "not_observed",
                "launcher_returncode": godot_rc if "godot_rc" in locals() else None,
            }
        )
        route_receipt["server_exit_code"] = server.poll()
        route_receipt["completion_signal_observed"] = "godot_rc" in locals() and not detached_exit_unknown
        _launch_guard.record_completion(out_dir, route_receipt["godot_exit_code"])
        route_receipt_path.write_text(
            json.dumps(route_receipt, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        # FL-4888: the final receipt leaves disposable .run storage in the same
        # execution. A receipt that cannot be preserved fails the run.
        try:
            preserved_manifest = run_receipt_preservation.preserve_run_receipt(
                repo_root=ROOT,
                receipt_path=route_receipt_path,
                fl_id=RUN_RECEIPT_FL_ID,
                runner="scripts/launch_fl4359_manual_play.py",
                destination_root=DURABLE_RECEIPT_ROOT,
                companion_names=("window_activation.json",),
            )
        except (OSError, RuntimeError) as error:
            print(f"[fl4359-manual] ERROR: route receipt not preserved: {error}", flush=True)
            raise SystemExit(8) from error
        print(f"[fl4359-manual] route receipt preserved {_repo_relative(preserved_manifest)}", flush=True)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# =============================================================
# package_asciicker_presenter_html_mac.py
# =============================================================
# Tracked macOS ONE-COMMAND packaging + launch front door for
# the FL-4588 ASCIID FL4512 Presenter HTML Authoring client.
#
# What this owns (and nothing else):
#   1. Build the current native ASCIID FL4512 presenter via
#      apps/asciicker-presenter/makefile_asciiid_mac → .run/asciicker-presenter
#   2. Build the HTML authoring shell (npm run build)
#   3. Copy the fresh shell, registry/schema, native backend, configuration,
#      and assets directly into one macOS app envelope
#      at dist/ASCIID FL4512 Presenter HTML Authoring.app.
#      Contents/Resources/bin/asciicker-presenter owns base_path; the complete
#      assets/ tree plus asciicker.cfg live beside that binary.
#   5. Start the authenticated loopback authoring runtime from the
#      packaged Resources paths (--authoring-port 43124)
#   6. Serve the bundled shell on loopback, open Google Chrome, deliver
#      the authoring credential through a same-origin in-memory bootstrap,
#      and write a
#      fail-closed runtime receipt that names Chrome as the UI app
#
# UI ownership (hard rule — packaging lane):
#   - Google Chrome owns the only visible product UI (HTML shell).
#   - Launch order: native runtime first, then health product_ready
#     (snapshot_ready + termpp_final_ready), then exactly one Chrome --app= shell.
#   - Backend death closes that Chrome app shell. A disconnected
#     HTML window must never remain presented as the editor.
#   - The C++ runtime exposes one read-only termpp_final frame identity without
#     exposing a second product window or editor renderer.
#   - Receipts are diagnostics only. Native stdout markers, window
#     discovery, and LaunchServices exits are never physical-action
#     claims and never product success by themselves.
#
# App-bundle path rule:
#   After bundling, launch must use Contents/Resources only. It must
#   not reach back into the git checkout for bin/shell/registry/launcher
#   or for assets/asciicker.cfg. Native recognizes only the exact
#   development-bin suffix <checkout>/.run/; nested package paths keep their
#   self-contained bin directory as base_path.
#
# What this deliberately does NOT own:
#   - editor/asciiid.cpp, application-service, HTTP transport,
#     registry, migration matrix, terrain code, analyze_runs.py
#     (other lanes own those shared paths)
#   - ImGui mutation cutover (FL-4588 product handlers)
#   - A second compatibility launcher that shadows this one
#
# Command:
#   python3 scripts/package_asciicker_presenter_html_mac.py
#   python3 scripts/package_asciicker_presenter_html_mac.py --package-only
#   python3 scripts/package_asciicker_presenter_html_mac.py --dry-run
# =============================================================

from __future__ import annotations

import ctypes

import argparse
import base64
import errno
import hashlib
import json
import os
import re
import socket
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from fl4588_ui_registry_identity import (
    RegistryIdentityError,
    validate_registry_identity,
)


def _resolve_module_root() -> Path:
    """
    Resolve the module "root" for package-time repo paths.

    When this file is copied into an .app Contents/Resources tree, parents[1]
    is Contents/ — not the git checkout. In that case treat Resources itself
    as the local root so accidental joins cannot walk into the wrong tree.
    Launch paths still rebind explicitly via
    ASCIICKER_PRESENTER_HTML_APP_RESOURCES.
    """
    here = Path(__file__).resolve()
    if here.parent.name == "Resources":
        macos_dir = here.parent.parent / "MacOS"
        if macos_dir.is_dir():
            return here.parent
    return here.parents[1]


ROOT = _resolve_module_root()
_SCRIPT_FILE = Path(__file__).resolve()
_REGISTRY_IDENTITY_FILE = _SCRIPT_FILE.with_name("fl4588_ui_registry_identity.py")
MAKEFILE = ROOT / "apps/asciicker-presenter/makefile_asciiid_mac"
PRESENTER_BIN = ROOT / ".run" / "asciicker-presenter"
SHELL_DIR = ROOT / "web" / "asciiid-authoring-shell"
SHELL_DIST = SHELL_DIR / "dist"
REGISTRY = ROOT / "assets" / "asciiid" / "ui" / "asciiid.ui_registry.v1.json"
REGISTRY_SCHEMA = (
    ROOT / "assets" / "asciiid" / "ui" / "asciiid.ui_registry.v1.schema.json"
)
REPO_ASSETS = ROOT / "assets"
REPO_CFG = ROOT / "asciicker.cfg"

APP_BUNDLE_NAME = "ASCIID FL4512 Presenter HTML Authoring.app"
APP_BUNDLE = ROOT / "dist" / APP_BUNDLE_NAME
LEGACY_RUN_APP_BUNDLE = ROOT / ".run" / APP_BUNDLE_NAME
RETIRED_ASCIIID_HTML_BUNDLES = (
    ROOT / "dist" / "ASCIIID HTML.app",
    ROOT / ".run" / "ASCIIID HTML.app",
    ROOT / "dist" / "ASCIID FL4512 HTML.app",
    ROOT / ".run" / "ASCIID FL4512 HTML.app",
)
LEGACY_FLAT_PACKAGE_ROOT = ROOT / ".run" / "asciiid_html_desktop"
APP_EXECUTABLE_NAME = "ASCIID FL4512 Presenter HTML Authoring"
APP_BUNDLE_ID = "world.riki.asciicker.presenter.html"
APP_RESOURCES_ENV = "ASCIICKER_PRESENTER_HTML_APP_RESOURCES"
FRESHNESS_RECEIPT_NAME = "freshness_receipt.json"
NATIVE_BUILD_INPUT_RELS = (
    "apps/asciicker-presenter/makefile_asciiid_mac",
    "apps/asciicker-presenter",
    "apps/fl4512-runtime",
    "engine",
    "platform",
    "server",
    "vendor/imgui",
)
SHELL_BUILD_INPUT_RELS = (
    "web/asciiid-authoring-shell/index.html",
    "web/asciiid-authoring-shell/package.json",
    "web/asciiid-authoring-shell/package-lock.json",
    "web/asciiid-authoring-shell/tsconfig.json",
    "web/asciiid-authoring-shell/vite.config.ts",
    "web/asciiid-authoring-shell/src",
)

# =============================================================================
# Source-to-bundle provenance pathspecs (FL-4652 / FL-4512 scope correction)
# =============================================================================
# The freshness receipt proves "source-to-bundle identity": that the exact
# files compiled or copied INTO the app bundle match their committed source.
# Only the files listed here actually feed the bundle:
#   - the native build inputs (makefile, C++ engine/editor/platform/server,
#     bundled imgui) that produce the native binary;
#   - the web authoring-shell sources that Vite compiles into `dist/`;
#   - the whole `assets/` tree (which INCLUDES the UI registry + schema under
#     assets/asciiid/ui/), copied verbatim into the bundle;
#   - the runtime config file `asciicker.cfg`, included in the bundle;
#   - this packager script itself (the "front door") and the registry-identity
#     module, because both are hashed into the bundle identity.
#
# The `source_dirty` provenance flag must answer exactly ONE question:
# "are any of the files that produced THIS bundle uncommitted or modified?"
# It must NOT be a whole-repository `git status`. This project's checkout is a
# shared, multi-lane tree: dozens of unrelated in-flight edits (a Godot
# renderer cutover, other lanes' test scaffolding, the failure log, regenerated
# dist artifacts) live alongside these inputs and never touch the bundle.
# Coupling this bundle's provenance to those unrelated files would make an
# honest, byte-for-byte reproducible package appear "dirty" for reasons that
# have nothing to do with what it was built from. We therefore scope the
# dirtiness probe to precisely the bundle inputs below. The per-input sha256
# `source_to_bundle` map in `write_freshness_receipt` remains the exact,
# cryptographic proof that these inputs equal the bundled bytes; `source_dirty`
# is only the "are these inputs committed?" companion to that map.
def _relative_to_root(path: Path) -> str:
    """Best-effort repo-relative pathspec for a bundle-input absolute path.

    Packaging always runs from the repo root, so `relative_to(ROOT)` succeeds
    for real inputs. If ROOT is ever a rebased bundle Resources dir (launch
    path, never packaging), fall back to the bare name so the tuple below never
    raises at import time.
    """
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return path.name


SOURCE_PROVENANCE_PATHSPECS = (
    *NATIVE_BUILD_INPUT_RELS,          # makefile + C++ sources + imgui -> native binary
    *SHELL_BUILD_INPUT_RELS,           # shell sources -> Vite dist/ tree
    "assets",                          # entire assets/ tree copied into the bundle (incl. UI registry)
    "asciicker.cfg",                   # runtime config included in the bundle
    _relative_to_root(_SCRIPT_FILE),   # this packager script, hashed into bundle identity
    _relative_to_root(_REGISTRY_IDENTITY_FILE),  # registry-identity module, hashed into identity
)

# Runtime globals always default to the sole product envelope. Explicit
# configure_paths_from_app_resources rebases copied launchers and test fixtures.
PACKAGE_ROOT = APP_BUNDLE / "Contents" / "Resources"
PACKAGE_BIN = PACKAGE_ROOT / "bin" / "asciicker-presenter"
PACKAGE_SHELL = PACKAGE_ROOT / "shell"
PACKAGE_RECEIPT = PACKAGE_ROOT / "runtime_receipt.json"
PACKAGE_RECEIPT_NON_HEADED_SMOKE = (
    PACKAGE_ROOT / "runtime_receipt.non_headed_smoke.json"
)
PACKAGE_FRESHNESS_RECEIPT = PACKAGE_ROOT / FRESHNESS_RECEIPT_NAME

# Fail-closed required files relative to native base_path
# (= Contents/Resources/bin/ when the binary lives there).
# Glyph paths are generated catalogs the runtime loads; empty glyphs/manifests is deleted.
REQUIRED_RUNTIME_ASSET_RELS = (
    "asciicker.cfg",
    "assets/fonts/Roboto-Medium.ttf",
    "assets/icons/app.png",
    "assets/glyphs/generated/material.additive.v1.shape_catalog.json",
    "assets/glyphs/generated/material.morphology.v2.profile_tables.json",
    "assets/glyphs/generated/material.morphology.v2.shape_catalog.json",
    "assets/glyphs/generated/fl4208_water_role_scoring_manifest.json",
    "assets/glyphs/generated/fl4512.glyph_transform_relation.v2.json",
    "assets/a3d/game_map_y8.a3d",
    "assets/asciiid/ui/asciiid.ui_registry.v1.json",
    "assets/asciiid/ui/asciiid.ui_registry.v1.schema.json",
    "assets/palettes/palette.gz",
)

# Shell transport.ts hard-codes this origin. Changing it here without
# changing the shell would open a second silent owner — fail closed instead.
AUTHORING_PORT = 43124
CHROME_DEVTOOLS_PORT = 9222
# Polling /json/version after spawn — flags alone do not prove attachability.
CHROME_DEVTOOLS_READY_TIMEOUT_S = 15.0
CHROME_DEVTOOLS_READY_POLL_S = 0.1
CHROME_AUTHORING_READY_TIMEOUT_S = 45.0
CHROME_AUTHORING_READY_POLL_S = 0.25
SHELL_PORT = 43125
HEALTH_URL = f"http://127.0.0.1:{AUTHORING_PORT}/api/v1/health"
CHROME_DEVTOOLS_VERSION_URL = (
    f"http://127.0.0.1:{CHROME_DEVTOOLS_PORT}/json/version"
)
TOKEN_LINE_PREFIX = "[ASCIIID-WEB] Session token:"
AUTHORING_BOOTSTRAP_PATH = "/asciiid.authoring_bootstrap.v1.json"
AUTHORING_BOOTSTRAP_HEADER = "X-Asciiid-Bootstrap"
AUTHORING_SHELL_SURFACE_KIND = "asciiid.web_authoring_shell.v1"

# Readiness diagnostics are collected by the same read-only CDP owner that
# decides LAUNCHED.  Only these paths and bounded, redacted text may enter a
# receipt; request/response bodies, headers, credentials, and session ids do
# not cross this boundary.
LAUNCHER_DIAGNOSTIC_TEXT_LIMIT = 512
LAUNCHER_DIAGNOSTIC_ERROR_LIMIT = 8
LAUNCHER_READINESS_HTTP_PATHS = {
    "/asciiid.ui_registry.v1.json": "static_registry",
    AUTHORING_BOOTSTRAP_PATH: "authoring_bootstrap",
    "/api/v1/health": "native_health",
    "/api/v1/handshake": "authoring_handshake",
    "/api/v1/runtime-snapshot": "runtime_snapshot",
}

# Keep only a bounded diagnostic tail from native stdout so the drain
# thread cannot grow without limit while still proving the pipe is emptied.
STDOUT_DIAGNOSTIC_TAIL_CHARS = 8000

# Headed product UI is Google Chrome only — not Safari, not a generic
# webbrowser.open() default, and never a visible native SDL window.
CHROME_APP_NAME = "Google Chrome"
CHROME_BIN = Path(
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
)
UI_APPLICATION_CHROME = "Google Chrome"

# Forbidden on-screen native product titles. Any match fails HTML-only launch.
FORBIDDEN_NATIVE_WINDOW_TITLE_SUBSTRINGS = (
    "GAME TERM",
    "ASCIIID Edit",
    "ASCIIID Term",
)

# Page <title> from the bundled shell index.html. A visible Chrome window
# must carry this substring before browser_visibility_verified may flip.
CHROME_SHELL_WINDOW_TITLE_SUBSTRING = "ASCIID FL4512 Presenter"

# How long the launcher waits for an on-screen Chrome shell window.
CHROME_VISIBILITY_TIMEOUT_S = 45.0


# LaunchServices / Chrome spawn exit 0 is request-accepted only.
# Visible browser / HTML load / physical input stay unverified until observed.
# Receipts record diagnostic launch state — never physical-action claims.
PROOF_STATUS_HEADED_LAUNCH_PENDING = "UNPROVEN_HEADED_LAUNCH_PENDING"
# Diagnostic only: Chrome visible + zero forbidden native windows observed.
# Hard-deleted owners: stdout markers, TERM++ windows, ImGui windows,
# and disconnected Chrome as product success.
DIAGNOSTIC_STATUS_HTML_ONLY_VISIBILITY = "HTML_ONLY_VISIBILITY_DIAGNOSTIC"

# Health keys that must all be true before Chrome may open (FL-4588).
# Capability ok alone is not product readiness — see authoring health.
REQUIRED_HEALTH_PRODUCT_READY_KEYS = (
    "product_ready",
    "mutation_enabled",
    "snapshot_ready",
    "termpp_final_ready",
)


class PackagingError(RuntimeError):
    """Fail-closed packaging/launch failure with a short operator message."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assert_registry_editor_cutover_complete(registry_path: Path) -> None:
    """Refuse to package while product cutover debt remains.

    Empty remaining-owner / missing-handler lists are valid completion.
    Nonempty missing handlers, surviving migrate owners, missing product
    interactions, or unowned live button/tab actions fail closed.
    """
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    leaves = registry.get("leaves")
    interactions = registry.get("interactions")
    if not isinstance(leaves, list) or not isinstance(interactions, list):
        raise PackagingError("authoritative UI registry has no leaf/interaction arrays")

    missing_leaf_handlers = sorted(
        str(leaf.get("leaf_id") or leaf.get("control_uid") or "<unknown-leaf>")
        for leaf in leaves
        if leaf.get("migration_status") == "migrate"
        and not leaf.get("application_service_handler")
    )
    remaining_native_owners = sorted(
        str(leaf.get("leaf_id") or leaf.get("control_uid") or "<unknown-leaf>")
        for leaf in leaves
        if leaf.get("migration_status") == "migrate"
        and leaf.get("old_owner_deleted") is not True
    )
    missing_product_interactions = sorted(
        str(
            interaction.get("leaf_id")
            or interaction.get("control_uid")
            or interaction.get("action_schema")
            or "<unknown-interaction>"
        )
        for interaction in interactions
        if interaction.get("action_scope") == "product_transaction"
        and not interaction.get("application_service_handler")
    )
    missing_browser_draft_interactions = sorted(
        str(
            interaction.get("leaf_id")
            or interaction.get("control_uid")
            or interaction.get("action_schema")
            or "<unknown-browser-draft>"
        )
        for interaction in interactions
        if interaction.get("action_scope") == "browser_draft"
        and not interaction.get("application_service_handler")
    )
    browser_owned_document_buttons = {
        "workspace.document.open.open",
        "workspace.document.save_as.open",
    }
    unowned_live_buttons = sorted(
        str(leaf.get("leaf_id") or leaf.get("control_uid") or "<unknown-leaf>")
        for leaf in leaves
        if leaf.get("status") == "live"
        and leaf.get("control_kind") in {"button", "tab"}
        and not leaf.get("application_service_handler")
        and not str(leaf.get("leaf_id") or "").startswith("workspace.navigation.")
        and leaf.get("leaf_id") not in browser_owned_document_buttons
    )
    if (
        missing_leaf_handlers
        or remaining_native_owners
        or missing_product_interactions
        or missing_browser_draft_interactions
        or unowned_live_buttons
    ):
        raise PackagingError(
            "HTML editor cutover incomplete: "
            f"missing_leaf_handlers={missing_leaf_handlers!r} "
            f"remaining_native_owners={remaining_native_owners!r} "
            f"missing_product_interactions={missing_product_interactions!r} "
            f"missing_browser_draft_interactions={missing_browser_draft_interactions!r} "
            f"unowned_live_buttons={unowned_live_buttons!r}"
        )


def _run(cmd: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
    completed = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise PackagingError(
            f"command failed ({completed.returncode}): {' '.join(cmd)}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )


def _git_head() -> str | None:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def _git_dirty(pathspecs: tuple[str, ...] = SOURCE_PROVENANCE_PATHSPECS) -> bool | None:
    """Return True when any *bundle-input* file is uncommitted or modified.

    This is the ``source_dirty`` provenance signal baked into the freshness
    receipt. It answers exactly one question: are any of the files that were
    compiled or copied INTO this app bundle uncommitted or dirty right now?

    It is deliberately SCOPED to the bundle inputs (see
    ``SOURCE_PROVENANCE_PATHSPECS`` above) rather than a whole-repository
    status. This checkout is a shared multi-lane tree; unrelated in-flight
    edits (other renderers, other lanes' tests, the failure log, regenerated
    dist artifacts) never enter this bundle and must not make an otherwise
    byte-identical package look dirty. Callers pass no argument in production;
    the ``pathspecs`` parameter exists only so tests can probe a narrower set.
    """
    completed = subprocess.run(
        # `--` separates the pathspecs so git limits the status report to only
        # the bundle-input files. `--untracked-files=all` still surfaces a NEW
        # (never-committed) bundle input, which IS a real provenance problem.
        [
            "git", "status", "--porcelain=v1", "--untracked-files=all",
            "--", *pathspecs,
        ],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        return None
    return bool(completed.stdout.strip())


def _relpath(path: Path) -> str:
    """Prefer a repo-relative path; keep absolute paths for test sandboxes."""
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def _registry_contract_hash(path: Path) -> str:
    """Recompute and validate the registry contract body identity."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PackagingError(f"registry contract unreadable at {path}: {exc}") from exc
    try:
        return validate_registry_identity(payload)
    except RegistryIdentityError as exc:
        raise PackagingError(f"registry contract identity invalid at {path}: {exc}") from exc


def write_receipt(payload: dict[str, Any]) -> Path:
    """Write the fail-closed receipt. Missing required fields refuse the write."""
    required = (
        "schema",
        "status",
        "created_at_utc",
        "package_root",
        "authoring_port",
        "shell_port",
        "fail_closed",
    )
    missing = [key for key in required if key not in payload]
    if missing:
        raise PackagingError(f"receipt missing required fields: {missing}")
    PACKAGE_RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    PACKAGE_RECEIPT.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return PACKAGE_RECEIPT


def fail_receipt(reason: str, **extra: Any) -> Path:
    """Record a failed packaging/launch attempt so absence is visible."""
    payload = {
        "schema": "asciiid.html_desktop_runtime_receipt.v1",
        "status": "FAIL",
        "created_at_utc": _utc_now(),
        "package_root": _relpath(PACKAGE_ROOT),
        "authoring_port": AUTHORING_PORT,
        "shell_port": SHELL_PORT,
        "fail_closed": True,
        "reason": reason,
        "git_head": _git_head(),
        **extra,
    }
    return write_receipt(payload)


# =============================================================
# FL-4588 evidence-viewer credential handoff
# =============================================================
# The HTML shell is the ONE authoring session; its plaintext session token lives
# only in process memory and crosses into the browser through the same-origin
# no-store bootstrap response. A separate read-only "evidence viewer"
# (capture tooling that reads authenticated frames / snapshots / events) must be
# able to run its own read-only handshake WITHOUT scraping the browser's private
# session state. This ephemeral handoff is how it gets a credential: a single
# 0600 file, written under the live package runtime directory once the launch is
# up, and deleted on every lifecycle exit so the credential never outlives the
# session.
#
# The credential is the DERIVED token hash the native handshake actually checks
# (sha256 of the trimmed, lowercased token — exactly what transport.ts sends),
# never the plaintext token. So the plaintext token appears nowhere on disk —
# not in the handoff, not in receipts, not in logs.
EVIDENCE_VIEWER_HANDOFF_NAME = "evidence_viewer_handoff.json"
EVIDENCE_VIEWER_SURFACE_KIND = "asciiid.web_evidence_viewer.v1"
RUNTIME_STATE_DIR_OVERRIDE: Path | None = None


def _evidence_viewer_handoff_path() -> Path:
    """Path of the ephemeral handoff under the CURRENT package runtime dir.

    PACKAGE_ROOT is rebased to the .app Resources tree when the launcher runs
    from a bundle, so this is computed live to always match the active runtime
    directory rather than a stale module-load-time value."""
    state_dir = RUNTIME_STATE_DIR_OVERRIDE or PACKAGE_ROOT
    return state_dir / EVIDENCE_VIEWER_HANDOFF_NAME


def delete_evidence_viewer_handoff() -> bool:
    """Delete the ephemeral evidence-viewer handoff if present.

    Best-effort and resilient: shutdown / exception / stale-recovery cleanup must
    never crash on a missing or already-removed file. Returns True only when a
    file was actually removed. A stubborn file (e.g. permission error) does not
    abort cleanup — the next launch's delete-first plus the atomic fail-closed
    write guarantee no stale credential survives into a new session."""
    handoff_path = _evidence_viewer_handoff_path()
    try:
        handoff_path.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def write_evidence_viewer_handoff(
    *,
    session_token: str,
    backend_pid: int,
    git_head: str | None,
    ui_registry_sha256: str,
    native_origin: str,
    shell_origin: str,
) -> Path:
    """Atomically write the ONE 0600 ephemeral evidence-viewer handoff.

    Delete-first (never overwrite a stale credential in place), atomic (write to
    a private temp in the same directory, force 0600, then rename over the
    target), and fail-closed (any permission or replacement failure raises and
    leaves no loose-permission or partial file behind).

    Arguments are the provenance the read-only viewer needs to bind its own
    handshake to this exact backend: the derived token hash (credential), the
    backend pid, the git head, the registry contract hash, and both origins.
    session_token is hashed here and never stored or logged in plaintext."""
    handoff_path = _evidence_viewer_handoff_path()
    # Delete-first: clear any stale handoff before writing the current one.
    delete_evidence_viewer_handoff()

    # Derived credential only: sha256 of the trimmed, lowercased session token,
    # matching transport.ts performHandshake (sha256Hex(trimmed.toLowerCase())).
    token_sha256 = hashlib.sha256(
        session_token.strip().lower().encode("utf-8")
    ).hexdigest()
    payload = {
        "schema": EVIDENCE_VIEWER_SURFACE_KIND,
        "read_only_surface_kind": EVIDENCE_VIEWER_SURFACE_KIND,
        "access": "read_only",
        "created_at_utc": _utc_now(),
        # Credential the native read-only handshake accepts (no plaintext token).
        "session_token_sha256": token_sha256,
        # Provenance so the viewer binds to this exact backend/session.
        "native_origin": native_origin,
        "shell_origin": shell_origin,
        "backend_pid": backend_pid,
        "git_head": git_head,
        "ui_registry_sha256": ui_registry_sha256,
    }
    body = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")

    directory = handoff_path.parent
    directory.mkdir(parents=True, exist_ok=True)
    tmp_path = handoff_path.with_name(handoff_path.name + ".tmp")
    try:
        # Remove any leftover temp from a crashed prior write so O_EXCL holds.
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        # O_EXCL + mode 0600: create a private file no other user can read.
        fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, body)
            os.fsync(fd)
        finally:
            os.close(fd)
        # Force exact 0600 regardless of the process umask, then rename over the
        # target atomically (the rename preserves the temp's 0600 mode).
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, handoff_path)
    except OSError as exc:
        # Fail closed: never leave a partial or loose-permission handoff behind.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise PackagingError(
            f"evidence viewer handoff write failed (fail closed): {exc}"
        )

    # Verify the final permissions are exactly 0600 — fail closed otherwise so a
    # world/group-readable credential can never be presented as valid.
    final_mode = handoff_path.stat().st_mode & 0o777
    if final_mode != 0o600:
        delete_evidence_viewer_handoff()
        raise PackagingError(
            "evidence viewer handoff permissions not 0600 "
            f"(got {oct(final_mode)}) — fail closed"
        )
    return handoff_path


def build_presenter_runtime() -> Path:
    """Rebuild .run/asciicker-presenter from the live tree using the mac product makefile."""
    if sys.platform != "darwin":
        raise PackagingError("this packaging front door is macOS-only")
    if not MAKEFILE.is_file():
        raise PackagingError(f"missing makefile: {MAKEFILE}")
    _run(["make", "-f", str(MAKEFILE), str(PRESENTER_BIN.relative_to(ROOT))], cwd=ROOT)
    if not PRESENTER_BIN.is_file():
        raise PackagingError(f"native binary missing after build: {PRESENTER_BIN}")
    return PRESENTER_BIN


def build_html_shell() -> Path:
    """Production-build the Vite authoring shell into web/.../dist."""
    if not (SHELL_DIR / "package.json").is_file():
        raise PackagingError(f"missing shell package.json under {SHELL_DIR}")
    # Prefer a local node_modules install when present; otherwise npm ci/install.
    if not (SHELL_DIR / "node_modules").is_dir():
        lock = SHELL_DIR / "package-lock.json"
        _run(
            ["npm", "ci" if lock.is_file() else "install"],
            cwd=SHELL_DIR,
        )
    _run(["npm", "run", "build"], cwd=SHELL_DIR)
    if not (SHELL_DIST / "index.html").is_file():
        raise PackagingError(f"shell build produced no index.html at {SHELL_DIST}")
    if not (SHELL_DIST / "asciiid.ui_registry.v1.json").is_file():
        raise PackagingError(
            "shell dist is missing asciiid.ui_registry.v1.json "
            "(vite publicDir must copy assets/asciiid/ui)"
        )
    if not (SHELL_DIST / "asciiid.ui_registry.v1.schema.json").is_file():
        raise PackagingError(
            "shell dist is missing asciiid.ui_registry.v1.schema.json"
        )
    if _sha256_file(SHELL_DIST / REGISTRY.name) != _sha256_file(REGISTRY):
        raise PackagingError("shell build registry bytes are stale")
    if _sha256_file(SHELL_DIST / REGISTRY_SCHEMA.name) != _sha256_file(
        REGISTRY_SCHEMA
    ):
        raise PackagingError("shell build registry schema bytes are stale")
    return SHELL_DIST


def _remove_package_tree_fail_closed(path: Path) -> None:
    """Remove one owned package tree, tolerating one macOS metadata race."""
    if not path.exists():
        return
    try:
        shutil.rmtree(path)
        return
    except OSError as exc:
        if exc.errno != errno.ENOTEMPTY:
            raise PackagingError(f"package tree removal failed: {path}: {exc}") from exc

    # Finder can recreate .DS_Store between rmtree's directory scan and its
    # final rmdir. Permit exactly that metadata residue once. Any product file,
    # receipt, socket, asset, plus executable residue still fails closed so an
    # active package writer cannot be mistaken for a harmless Finder race.
    residue_files = sorted(
        candidate
        for candidate in path.rglob("*")
        if candidate.is_file() or candidate.is_symlink()
    )
    unexpected = [candidate for candidate in residue_files if candidate.name != ".DS_Store"]
    if unexpected:
        rendered = ", ".join(str(candidate.relative_to(path)) for candidate in unexpected)
        raise PackagingError(
            f"package tree removal left non-metadata residue under {path}: {rendered}"
        )
    for candidate in residue_files:
        candidate.unlink()
    try:
        shutil.rmtree(path)
    except OSError as exc:
        raise PackagingError(
            f"package tree removal failed after Finder-metadata cleanup: {path}: {exc}"
        ) from exc


def app_bundle_layout(bundle: Path | None = None) -> dict[str, Path]:
    """Return the standard Contents/MacOS/Resources paths for the .app envelope."""
    bundle_path = (bundle or APP_BUNDLE).resolve()
    contents = bundle_path / "Contents"
    resources = contents / "Resources"
    native_bin_dir = resources / "bin"
    return {
        "bundle": bundle_path,
        "contents": contents,
        "macos": contents / "MacOS",
        "resources": resources,
        "executable": contents / "MacOS" / APP_EXECUTABLE_NAME,
        "plist": contents / "Info.plist",
        "front_door": resources / "package_asciicker_presenter_html_mac.py",
        "registry_identity": resources / "fl4588_ui_registry_identity.py",
        "native_bin_dir": native_bin_dir,
        "native_bin": native_bin_dir / "asciicker-presenter",
        "native_cfg": native_bin_dir / "asciicker.cfg",
        "native_assets": native_bin_dir / "assets",
        "asset_manifest": native_bin_dir / "asset_manifest.json",
        "shell_index": resources / "shell" / "index.html",
        "registry": resources / "asciiid.ui_registry.v1.json",
        "registry_schema": resources / "asciiid.ui_registry.v1.schema.json",
        "freshness_receipt": resources / FRESHNESS_RECEIPT_NAME,
        "layout": resources / "LAYOUT.txt",
    }


def _hash_path(path: Path) -> str:
    """SHA-256 a file, or a non-empty directory of sorted file digests."""
    if path.is_file():
        return _sha256_file(path)
    if not path.is_dir():
        raise PackagingError(f"required runtime asset missing: {path}")
    digest = hashlib.sha256()
    file_count = 0
    for child in sorted(path.rglob("*")):
        if not child.is_file():
            continue
        file_count += 1
        rel = child.relative_to(path).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(_sha256_file(child)))
    if file_count == 0:
        raise PackagingError(
            f"required runtime asset directory is empty: {path}"
        )
    return digest.hexdigest()


def _canonical_json_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _hash_named_paths(root: Path, rels: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    for rel in rels:
        path = root / rel
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_hash_path(path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def current_source_build_identity() -> dict[str, str]:
    return {
        "native_build_inputs_sha256": _hash_named_paths(
            ROOT, NATIVE_BUILD_INPUT_RELS
        ),
        "shell_build_inputs_sha256": _hash_named_paths(
            ROOT, SHELL_BUILD_INPUT_RELS
        ),
    }


def _freshness_receipt_hash(payload: dict[str, Any]) -> str:
    unhashed = json.loads(json.dumps(payload))
    unhashed["freshness_receipt_sha256"] = ""
    return _canonical_json_sha256(unhashed)


def _read_asset_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PackagingError(f"asset manifest unreadable at {path}: {exc}") from exc
    if payload.get("schema") != "asciiid.html_desktop_app_asset_manifest.v1":
        raise PackagingError("asset manifest schema mismatch")
    manifest_sha256 = payload.get("manifest_sha256")
    if not isinstance(manifest_sha256, str) or len(manifest_sha256) != 64:
        raise PackagingError("asset manifest identity missing")
    entries = payload.get("entries")
    if not isinstance(entries, list) or payload.get("required_count") != len(entries):
        raise PackagingError("asset manifest entry count mismatch")
    rollup = hashlib.sha256()
    for entry in entries:
        if not isinstance(entry, dict):
            raise PackagingError("asset manifest entry malformed")
        path_value = entry.get("path")
        hash_value = entry.get("sha256")
        if not isinstance(path_value, str) or not isinstance(hash_value, str):
            raise PackagingError("asset manifest entry identity malformed")
        rollup.update(path_value.encode("utf-8"))
        rollup.update(b"\0")
        rollup.update(hash_value.encode("utf-8"))
        rollup.update(b"\n")
    if rollup.hexdigest() != manifest_sha256:
        raise PackagingError("asset manifest hash mismatch")
    return payload


def _bundle_freshness_facts(resources: Path) -> dict[str, Any]:
    paths = app_bundle_layout(resources.parent.parent)
    manifest = _read_asset_manifest(paths["asset_manifest"])
    runtime_registry = paths["native_assets"] / "asciiid/ui/asciiid.ui_registry.v1.json"
    runtime_schema = (
        paths["native_assets"] / "asciiid/ui/asciiid.ui_registry.v1.schema.json"
    )
    shell_registry = paths["resources"] / "shell/asciiid.ui_registry.v1.json"
    shell_schema = paths["resources"] / "shell/asciiid.ui_registry.v1.schema.json"
    return {
        "native_sha256": _sha256_file(paths["native_bin"]),
        "shell_tree_sha256": _hash_path(paths["resources"] / "shell"),
        "shell_registry_sha256": _sha256_file(shell_registry),
        "shell_registry_contract_sha256": _registry_contract_hash(shell_registry),
        "shell_registry_schema_sha256": _sha256_file(shell_schema),
        "top_registry_sha256": _sha256_file(paths["registry"]),
        "top_registry_contract_sha256": _registry_contract_hash(paths["registry"]),
        "top_registry_schema_sha256": _sha256_file(paths["registry_schema"]),
        "runtime_registry_sha256": _sha256_file(runtime_registry),
        "runtime_registry_contract_sha256": _registry_contract_hash(runtime_registry),
        "runtime_registry_schema_sha256": _sha256_file(runtime_schema),
        "assets_tree_sha256": _hash_path(paths["native_assets"]),
        "config_sha256": _sha256_file(paths["native_cfg"]),
        "front_door_sha256": _sha256_file(paths["front_door"]),
        "registry_identity_sha256": _sha256_file(paths["registry_identity"]),
        "app_executable_sha256": _sha256_file(paths["executable"]),
        "info_plist_sha256": _sha256_file(paths["plist"]),
        "layout_sha256": _sha256_file(paths["layout"]),
        "asset_manifest_file_sha256": _sha256_file(paths["asset_manifest"]),
        "asset_manifest_sha256": manifest["manifest_sha256"],
    }


def _source_freshness_facts(
    *,
    native_bin: Path,
    shell_dist: Path,
    assets_src: Path,
    cfg_src: Path,
) -> dict[str, Any]:
    return {
        **current_source_build_identity(),
        "native_sha256": _sha256_file(native_bin),
        "shell_tree_sha256": _hash_path(shell_dist),
        "registry_sha256": _sha256_file(REGISTRY),
        "registry_contract_sha256": _registry_contract_hash(REGISTRY),
        "registry_schema_sha256": _sha256_file(REGISTRY_SCHEMA),
        "assets_tree_sha256": _hash_path(assets_src),
        "config_sha256": _sha256_file(cfg_src),
        "front_door_sha256": _sha256_file(_SCRIPT_FILE),
        "registry_identity_sha256": _sha256_file(_REGISTRY_IDENTITY_FILE),
    }


def write_freshness_receipt(
    resources: Path,
    *,
    native_bin: Path,
    shell_dist: Path,
    assets_src: Path,
    cfg_src: Path,
    source_git_head: str | None = None,
    source_dirty: bool | None = None,
) -> dict[str, Any]:
    """Seal current source inputs to the exact self-contained app bytes."""
    if source_git_head is None:
        source_git_head = _git_head()
    if source_dirty is None:
        source_dirty = _git_dirty()
    source = _source_freshness_facts(
        native_bin=native_bin,
        shell_dist=shell_dist,
        assets_src=assets_src,
        cfg_src=cfg_src,
    )
    bundle = _bundle_freshness_facts(resources)
    source_to_bundle = {
        "native": source["native_sha256"] == bundle["native_sha256"],
        "shell": source["shell_tree_sha256"] == bundle["shell_tree_sha256"],
        "registry_top": source["registry_sha256"] == bundle["top_registry_sha256"],
        "registry_shell": source["registry_sha256"] == bundle["shell_registry_sha256"],
        "registry_runtime": source["registry_sha256"] == bundle["runtime_registry_sha256"],
        "registry_schema_top": (
            source["registry_schema_sha256"] == bundle["top_registry_schema_sha256"]
        ),
        "registry_schema_shell": (
            source["registry_schema_sha256"] == bundle["shell_registry_schema_sha256"]
        ),
        "registry_schema_runtime": (
            source["registry_schema_sha256"] == bundle["runtime_registry_schema_sha256"]
        ),
        "assets": source["assets_tree_sha256"] == bundle["assets_tree_sha256"],
        "config": source["config_sha256"] == bundle["config_sha256"],
        "front_door": source["front_door_sha256"] == bundle["front_door_sha256"],
        "registry_identity": (
            source["registry_identity_sha256"]
            == bundle["registry_identity_sha256"]
        ),
    }
    if not all(source_to_bundle.values()):
        failed = sorted(key for key, matched in source_to_bundle.items() if not matched)
        raise PackagingError(
            f"source-to-bundle freshness mismatch: {failed}"
        )
    package_identity = {
        "source_git_head": source_git_head,
        "source_dirty": source_dirty,
        "source": source,
        "bundle": bundle,
        "source_to_bundle": source_to_bundle,
    }
    receipt = {
        "schema": "asciiid.html_desktop_freshness_receipt.v1",
        "status": "FRESH",
        "created_at_utc": _utc_now(),
        "fail_closed": True,
        "proof_status": "PROOF_OPEN",
        "headed_proof_claim": False,
        **package_identity,
        "package_identity_sha256": _canonical_json_sha256(package_identity),
        "freshness_receipt_sha256": "",
    }
    receipt["freshness_receipt_sha256"] = _freshness_receipt_hash(receipt)
    path = resources / FRESHNESS_RECEIPT_NAME
    path.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


def verify_freshness_receipt(resources: Path) -> dict[str, Any]:
    """Fail closed before native start when any packaged byte has drifted."""
    path = resources / FRESHNESS_RECEIPT_NAME
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PackagingError(f"freshness receipt missing or unreadable: {exc}") from exc
    if receipt.get("schema") != "asciiid.html_desktop_freshness_receipt.v1":
        raise PackagingError("freshness receipt schema mismatch")
    if receipt.get("status") != "FRESH" or receipt.get("fail_closed") is not True:
        raise PackagingError("freshness receipt is not fail-closed FRESH")
    if receipt.get("headed_proof_claim") is not False:
        raise PackagingError("freshness receipt must not claim headed proof")
    if receipt.get("freshness_receipt_sha256") != _freshness_receipt_hash(receipt):
        raise PackagingError("freshness receipt self-hash mismatch")
    package_identity = {
        "source_git_head": receipt.get("source_git_head"),
        "source_dirty": receipt.get("source_dirty"),
        "source": receipt.get("source"),
        "bundle": receipt.get("bundle"),
        "source_to_bundle": receipt.get("source_to_bundle"),
    }
    if receipt.get("package_identity_sha256") != _canonical_json_sha256(
        package_identity
    ):
        raise PackagingError("freshness package identity mismatch")
    source_to_bundle = receipt.get("source_to_bundle")
    if not isinstance(source_to_bundle, dict) or not source_to_bundle or not all(
        value is True for value in source_to_bundle.values()
    ):
        raise PackagingError("freshness receipt contains an unsealed source input")
    current_bundle = _bundle_freshness_facts(resources)
    if current_bundle != receipt.get("bundle"):
        raise PackagingError("packaged bytes drifted after freshness seal")
    return receipt


def write_runtime_asset_manifest(
    asset_base_dir: Path,
    *,
    required_rels: tuple[str, ...] = REQUIRED_RUNTIME_ASSET_RELS,
    source_git_head: str | None = None,
    source_dirty: bool | None = None,
) -> dict[str, Any]:
    """
    FL-4588 fail-shut manifest of required assets under the native base_path dir.

    This is the ONE and ONLY writer of ``asset_manifest.json`` for the sealed
    app bundle. Keeping it single-owner is deliberate (FL-4588): a copied
    writer could drift from the identity verified before native start.

    Parameters
    ----------
    asset_base_dir:
        The directory the native runtime treats as ``base_path`` — i.e. the
        directory the required runtime assets are hashed *relative to*. For the
        app bundle this is ``Contents/Resources/bin`` (assets are copied there).
    Missing required entries refuse the package (FL-4588 fail-shut) — standalone
    launch must not discover the gap only after BASE PATH points at an empty tree.
    """
    if source_git_head is None:
        source_git_head = _git_head()
    if source_dirty is None:
        source_dirty = _git_dirty()
    entries: list[dict[str, Any]] = []
    missing: list[str] = []
    for rel in required_rels:
        target = asset_base_dir / rel
        if not target.exists():
            missing.append(rel)
            continue
        entries.append(
            {
                "path": rel,
                "sha256": _hash_path(target),
                "kind": "dir" if target.is_dir() else "file",
            }
        )
    if missing:
        raise PackagingError(
            "app bundle runtime assets incomplete (FL-4588 fail-shut): "
            + ", ".join(missing)
        )
    packaged_registry = asset_base_dir / "assets/asciiid/ui/asciiid.ui_registry.v1.json"
    manifest = {
        "schema": "asciiid.html_desktop_app_asset_manifest.v1",
        "created_at_utc": _utc_now(),
        "source_commit": source_git_head,
        # Honest provenance (FL-4588): True whenever the source tree is dirty.
        # The editor still starts; runtime identity exposes proof_eligible=false
        # and the grouped-proof validator rejects the package before input.
        "source_dirty": source_dirty,
        "ui_registry_sha256": _registry_contract_hash(packaged_registry),
        # Records where the assets actually live for this launch (= base_path).
        "native_base_path_dir": str(asset_base_dir.resolve()),
        "required_count": len(entries),
        "entries": entries,
    }
    # Manifest digest over the stable entry list (path + sha256). Because this is
    # over the asset bytes, independently from its filesystem envelope.
    rollup = hashlib.sha256()
    for entry in entries:
        rollup.update(entry["path"].encode("utf-8"))
        rollup.update(b"\0")
        rollup.update(entry["sha256"].encode("utf-8"))
        rollup.update(b"\n")
    manifest["manifest_sha256"] = rollup.hexdigest()
    asset_base_dir.mkdir(parents=True, exist_ok=True)
    (asset_base_dir / "asset_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def bundle_native_runtime_tree(
    native_bin_dir: Path,
    *,
    assets_src: Path,
    cfg_src: Path,
    source_git_head: str | None = None,
    source_dirty: bool | None = None,
) -> dict[str, Any]:
    """
    Place asciicker.cfg + complete assets/ beside Contents/Resources/bin/asciicker-presenter.

    Native base_path becomes <...>/Contents/Resources/bin/ when the binary
    lives there, so assets must be <bin>/assets/ and cfg <bin>/asciicker.cfg.
    """
    if not assets_src.is_dir():
        raise PackagingError(f"runtime assets source missing: {assets_src}")
    if not cfg_src.is_file():
        raise PackagingError(f"asciicker.cfg missing: {cfg_src}")

    native_bin_dir.mkdir(parents=True, exist_ok=True)
    dest_assets = native_bin_dir / "assets"
    if dest_assets.exists():
        shutil.rmtree(dest_assets)
    shutil.copytree(assets_src, dest_assets)
    shutil.copy2(cfg_src, native_bin_dir / "asciicker.cfg")
    manifest = write_runtime_asset_manifest(
        native_bin_dir,
        source_git_head=source_git_head,
        source_dirty=source_dirty,
    )
    return {
        "app_runtime_assets": _relpath(dest_assets),
        "app_runtime_cfg": _relpath(native_bin_dir / "asciicker.cfg"),
        "app_asset_manifest": _relpath(native_bin_dir / "asset_manifest.json"),
        "app_asset_manifest_sha256": manifest["manifest_sha256"],
        "app_asset_required_count": manifest["required_count"],
        "app_asset_manifest_source_dirty": manifest["source_dirty"],
    }


def configure_paths_from_app_resources(resources: Path) -> Path:
    """
    Rebind package globals to an .app Contents/Resources tree.

    After this call, launch uses only Resources-owned bin/shell/registry/
    receipt paths. Repo-relative .run/asciiid_html_desktop paths are not used.
    """
    global PACKAGE_ROOT, PACKAGE_BIN, PACKAGE_SHELL, PACKAGE_RECEIPT
    global PACKAGE_RECEIPT_NON_HEADED_SMOKE, PACKAGE_FRESHNESS_RECEIPT
    resources = resources.resolve()
    native = resources / "bin" / "asciicker-presenter"
    shell_index = resources / "shell" / "index.html"
    freshness_receipt = resources / FRESHNESS_RECEIPT_NAME
    if not native.is_file():
        raise PackagingError(f"app Resources missing native runtime at {native}")
    if not shell_index.is_file():
        raise PackagingError(f"app Resources missing shell index at {shell_index}")
    if not freshness_receipt.is_file():
        raise PackagingError(
            f"app Resources missing freshness receipt at {freshness_receipt}"
        )
    PACKAGE_ROOT = resources
    PACKAGE_BIN = native
    PACKAGE_SHELL = resources / "shell"
    PACKAGE_RECEIPT = resources / "runtime_receipt.json"
    PACKAGE_RECEIPT_NON_HEADED_SMOKE = (
        resources / "runtime_receipt.non_headed_smoke.json"
    )
    PACKAGE_FRESHNESS_RECEIPT = resources / FRESHNESS_RECEIPT_NAME
    return resources


def build_app_bundle(
    *,
    native_bin: Path,
    shell_dist: Path,
    assets_src: Path | None = None,
    cfg_src: Path | None = None,
    source_git_head: str | None = None,
    source_dirty: bool | None = None,
) -> dict[str, Any]:
    """
    Build dist/ASCIID FL4512 Presenter HTML Authoring.app from current source build outputs.

    Sole package identity is the fail-closed freshness receipt plus the
    hash-pinned runtime asset manifest.

    Keyword args (exactly once each):
      native_bin — fresh make output
      shell_dist — fresh npm build output
      assets_src — optional runtime assets fixture
      cfg_src — optional asciicker.cfg fixture
    """
    if source_git_head is None:
        source_git_head = _git_head()
    if source_dirty is None:
        source_dirty = _git_dirty()
    if not native_bin.is_file():
        raise PackagingError(f"fresh native backend missing: {native_bin}")
    if not (shell_dist / "index.html").is_file():
        raise PackagingError(f"fresh shell build missing: {shell_dist}")
    if not (shell_dist / REGISTRY.name).is_file() or not (
        shell_dist / REGISTRY_SCHEMA.name
    ).is_file():
        raise PackagingError("fresh shell build lacks registry/schema")
    assert_registry_editor_cutover_complete(REGISTRY)
    if _SCRIPT_FILE.parent.name == "Resources":
        raise PackagingError(
            "refuse to rebuild the app envelope from inside an app Resources copy"
        )

    paths = app_bundle_layout()
    # Delete-first: neither legacy .run app nor flat launch package may survive
    # beside the sole self-contained macOS product envelope.
    if LEGACY_RUN_APP_BUNDLE.exists():
        shutil.rmtree(LEGACY_RUN_APP_BUNDLE)
    for retired_bundle in RETIRED_ASCIIID_HTML_BUNDLES:
        if retired_bundle.exists():
            shutil.rmtree(retired_bundle)
    _remove_package_tree_fail_closed(LEGACY_FLAT_PACKAGE_ROOT)

    if paths["bundle"].exists():
        shutil.rmtree(paths["bundle"])
    paths["macos"].mkdir(parents=True)
    paths["resources"].mkdir(parents=True)

    paths["native_bin_dir"].mkdir(parents=True)
    shutil.copy2(native_bin, paths["native_bin"])
    paths["native_bin"].chmod(paths["native_bin"].stat().st_mode | 0o111)

    runtime_facts = bundle_native_runtime_tree(
        paths["native_bin_dir"],
        assets_src=assets_src or REPO_ASSETS,
        cfg_src=cfg_src or REPO_CFG,
        source_git_head=source_git_head,
        source_dirty=source_dirty,
    )

    shutil.copytree(shell_dist, paths["resources"] / "shell")
    # Explicit authoritative copies prevent a stale Vite publicDir result from
    # becoming a second registry/schema owner.
    shutil.copy2(REGISTRY, paths["resources"] / "shell" / REGISTRY.name)
    shutil.copy2(
        REGISTRY_SCHEMA,
        paths["resources"] / "shell" / REGISTRY_SCHEMA.name,
    )
    shutil.copy2(REGISTRY, paths["registry"])
    shutil.copy2(REGISTRY_SCHEMA, paths["registry_schema"])
    # Self-contained Resources launch front door — no repo scripts path.
    shutil.copy2(_SCRIPT_FILE, paths["front_door"])
    shutil.copy2(_REGISTRY_IDENTITY_FILE, paths["registry_identity"])

    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>en</string>
  <key>CFBundleExecutable</key>
  <string>{APP_EXECUTABLE_NAME}</string>
  <key>CFBundleIdentifier</key>
  <string>{APP_BUNDLE_ID}</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>ASCIID FL4512 Presenter HTML Authoring</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>LSMinimumSystemVersion</key>
  <string>12.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
"""
    paths["plist"].write_text(plist, encoding="utf-8")

    # MacOS executable: Resources-only. Never walks back to the git checkout.
    macos_launcher = f"""#!/bin/sh
# ASCIID FL4512 Presenter HTML Authoring.app MacOS entry — Resources-owned launch only.
set -eu
DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
RESOURCES="$(CDPATH= cd -- "$DIR/../Resources" && pwd)"
export {APP_RESOURCES_ENV}="$RESOURCES"
exec /usr/bin/env python3 "$RESOURCES/package_asciicker_presenter_html_mac.py" --launch-from-app-resources
"""
    paths["executable"].write_text(macos_launcher, encoding="utf-8")
    paths["executable"].chmod(paths["executable"].stat().st_mode | 0o111)

    layout = f"""ASCIID FL4512 Presenter HTML Authoring.app (macOS one-launch envelope)
==================================================
{_relpath(paths["bundle"])}/
  Contents/Info.plist
  Contents/MacOS/{APP_EXECUTABLE_NAME}
  Contents/Resources/
    bin/asciicker-presenter     native base_path directory
    bin/asciicker.cfg
    bin/assets/                 complete runtime assets (not checkout)
    bin/asset_manifest.json
    shell/
    asciiid.ui_registry.v1.json
    package_asciicker_presenter_html_mac.py
    fl4588_ui_registry_identity.py
    freshness_receipt.json
    LAYOUT.txt

Product UI: Google Chrome --app= HTML shell (sole visible application)
Native runtime: hidden backend - zero visible ImGui / GAME TERM windows
Chrome open gate: health product_ready (snapshot + both frames matched)
Backend death: close Chrome app shell (no disconnected editor UI)
Freshness receipt: mandatory source-to-bundle identity; verified before native start
Runtime receipt: created only by a launch; diagnostic, never shipped as package identity
Path rule: self-contained native bin owns assets, config, and manifest at any envelope path
Launch paths: Contents/Resources only (no repo-relative package/asset paths)
Sole package identity: freshness_receipt.json + hash-pinned asset manifest
"""
    (paths["resources"] / "LAYOUT.txt").write_text(layout, encoding="utf-8")

    freshness = write_freshness_receipt(
        paths["resources"],
        native_bin=native_bin,
        shell_dist=shell_dist,
        assets_src=assets_src or REPO_ASSETS,
        cfg_src=cfg_src or REPO_CFG,
        source_git_head=source_git_head,
        source_dirty=source_dirty,
    )

    return {
        "app_bundle": _relpath(paths["bundle"]),
        "app_bundle_name": APP_BUNDLE_NAME,
        "app_executable": _relpath(paths["executable"]),
        "app_resources": _relpath(paths["resources"]),
        "app_native_sha256": _sha256_file(paths["native_bin"]),
        "app_shell_index_sha256": _sha256_file(paths["shell_index"]),
        "app_registry_sha256": _registry_contract_hash(paths["registry"]),
        "app_front_door_sha256": _sha256_file(paths["front_door"]),
        "freshness_receipt": _relpath(paths["freshness_receipt"]),
        "freshness_receipt_sha256": freshness["freshness_receipt_sha256"],
        "package_identity_sha256": freshness["package_identity_sha256"],
        "freshness_status": freshness["status"],
        "headed_proof_claim": False,
        "app_launch_self_contained": True,
        "app_native_base_path_self_contained": True,
        "visible_native_product_windows_required": 0,
        **runtime_facts,
    }


def package(
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Build current native plus shell directly into the sealed app envelope."""
    if dry_run:
        return {
            "schema": "asciiid.html_desktop_package_plan.v1",
            "status": "DRY_RUN",
            "makefile": "apps/asciicker-presenter/makefile_asciiid_mac",
            "native_out": ".run/asciicker-presenter",
            "shell_dir": "web/asciiid-authoring-shell",
            "app_bundle": f"dist/{APP_BUNDLE_NAME}",
            "authoring_port": AUTHORING_PORT,
            "shell_port": SHELL_PORT,
            "steps": [
                "make -f apps/asciicker-presenter/makefile_asciiid_mac .run/asciicker-presenter",
                "npm run build (web/asciiid-authoring-shell)",
                f"build dist/{APP_BUNDLE_NAME} directly from current outputs",
                "seal source-to-bundle freshness and asset manifest identities",
                "launch from app Resources + open Google Chrome",
            ],
        }

    source_git_head = _git_head()
    source_dirty = _git_dirty()
    build_inputs_before = current_source_build_identity()
    native = build_presenter_runtime()
    shell = build_html_shell()
    build_inputs_after = current_source_build_identity()
    if build_inputs_after != build_inputs_before:
        raise PackagingError(
            "source build inputs changed during packaging; rebuild from one frozen state"
        )
    return build_app_bundle(
        native_bin=native,
        shell_dist=shell,
        source_git_head=source_git_head,
        source_dirty=source_dirty,
    )


class _LoopbackShellHandler(SimpleHTTPRequestHandler):
    """Serve the bundled shell plus its in-memory authoring bootstrap."""

    def __init__(
        self,
        *args: Any,
        directory: str,
        session_token: str,
        **kwargs: Any,
    ) -> None:
        self._session_token = session_token
        super().__init__(*args, directory=directory, **kwargs)

    def do_GET(self) -> None:  # noqa: N802
        # FL-4588 delete-first: the authoring token no longer lives in the URL
        # fragment. A normal shell route bootstraps itself through this exact
        # same-origin request. The required non-simple header makes a hostile
        # cross-origin read preflight; this server grants no CORS permission.
        if self.path == AUTHORING_BOOTSTRAP_PATH:
            expected_host = f"127.0.0.1:{self.server.server_port}"
            if (
                self.headers.get(AUTHORING_BOOTSTRAP_HEADER)
                != AUTHORING_SHELL_SURFACE_KIND
                or self.headers.get("Host") != expected_host
            ):
                self.send_error(403, "authoring bootstrap rejected")
                return
            body = (
                json.dumps(
                    {
                        "schema": "asciiid.authoring_bootstrap.v1",
                        "surface_kind": AUTHORING_SHELL_SURFACE_KIND,
                        "session_token": self._session_token,
                    },
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        # Keep launch stdout readable; package receipt owns the recorded facts.
        return


def _start_shell_server(
    directory: Path,
    port: int,
    session_token: str,
) -> ThreadingHTTPServer:
    # Bind loopback only — the native authoring API also rejects non-loopback.
    server = ThreadingHTTPServer(
        ("127.0.0.1", port),
        lambda *args, **kwargs: _LoopbackShellHandler(
            *args,
            directory=str(directory),
            session_token=session_token,
            **kwargs,
        ),
    )
    thread = threading.Thread(target=server.serve_forever, name="asciiid-html-shell", daemon=True)
    thread.start()
    return server


def _wait_health(
    timeout_s: float = 45.0,
    *,
    health_url: str = HEALTH_URL,
) -> dict[str, Any]:
    """
    Poll /api/v1/health until product_ready is true.

    Packaging lane rule: do not open Chrome until the native backend reports
    mutation_enabled, snapshot_ready, termpp_final_ready, and product_ready. A non-mutating backend is not an
    editor and must never expose the shell.
    """
    deadline = time.time() + timeout_s
    last_error = "health_not_attempted"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(health_url, timeout=2.0) as response:
                body = response.read().decode("utf-8")
                payload = json.loads(body)
                if not isinstance(payload, dict) or not payload.get("ok"):
                    last_error = f"health_not_ok:{body[:200]}"
                else:
                    missing = [
                        key
                        for key in REQUIRED_HEALTH_PRODUCT_READY_KEYS
                        if payload.get(key) is not True
                    ]
                    if missing:
                        last_error = (
                            "product_not_ready:"
                            + ",".join(missing)
                            + f":body={body[:200]}"
                        )
                    else:
                        return payload
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            last_error = str(exc)
        time.sleep(0.25)
    raise PackagingError(f"authoring health failed closed: {last_error}")


def _redact_token_line(text: str) -> str:
    """FL-4588: replace the plaintext session token in a native stdout line with
    a placeholder before that line can reach the diagnostic tail.

    The native runtime prints '[ASCIIID-WEB] Session token: <token>' to stdout.
    The bounded diagnostic tail is written into the runtime receipt, so the raw
    token would otherwise land in a tracked-adjacent receipt and in launcher
    logs. Redacting here keeps the line visible as evidence the token was
    observed while never exposing the credential itself. Token extraction reads
    the original line separately, so redaction never breaks the handshake."""
    if TOKEN_LINE_PREFIX not in text:
        return text
    prefix, _, _rest = text.partition(TOKEN_LINE_PREFIX)
    trailing_newline = "\n" if text.endswith("\n") else ""
    return f"{prefix}{TOKEN_LINE_PREFIX} [redacted]{trailing_newline}"


class NativeStdoutTokenDrain:
    """
    Drain native stdout without deadlocking the pipe.

    Why this exists:
      The native process writes the session token, then keeps printing.
      If the Python side stops reading after the token line, the OS pipe
      buffer fills and the native process blocks on write — a classic
      stdout deadlock. This reader signals as soon as the token appears,
      then keeps consuming bytes through EOF while retaining only a
      bounded diagnostic tail for the runtime receipt.
    """

    def __init__(
        self,
        stream: Any,
        *,
        max_tail_chars: int = STDOUT_DIAGNOSTIC_TAIL_CHARS,
    ) -> None:
        self._stream = stream
        self._max_tail_chars = max_tail_chars
        # deque of recent line chunks; length trimmed by character budget.
        self._tail: deque[str] = deque()
        self._tail_chars = 0
        self._lock = threading.Lock()
        self._token_holder: dict[str, str] = {}
        self._token_event = threading.Event()
        self._eof_event = threading.Event()
        # Counts every byte read AFTER the token line — tests assert this.
        self.bytes_consumed_after_token = 0
        self._thread = threading.Thread(
            target=self._reader,
            name="asciiid-token-drain",
            daemon=True,
        )

    def start(self) -> None:
        """Begin draining the stream on a background thread."""
        self._thread.start()

    def _append_tail(self, text: str) -> None:
        """Keep only the newest max_tail_chars of diagnostic text."""
        with self._lock:
            self._tail.append(text)
            self._tail_chars += len(text)
            while self._tail_chars > self._max_tail_chars and self._tail:
                dropped = self._tail.popleft()
                self._tail_chars -= len(dropped)

    def _reader(self) -> None:
        """
        Read every line until EOF.

        Token discovery sets the event immediately; the loop never returns
        early — that early-return was the pipe-deadlock bug.
        """
        try:
            for line in self._stream:
                text = line if isinstance(line, str) else line.decode("utf-8", "replace")
                # Redact the token before it enters the diagnostic tail (which is
                # written into the runtime receipt). Extraction below still reads
                # the original line, so the handshake token is unaffected.
                self._append_tail(_redact_token_line(text))
                if "token" not in self._token_holder and TOKEN_LINE_PREFIX in text:
                    token = text.split(TOKEN_LINE_PREFIX, 1)[1].strip()
                    if token:
                        self._token_holder["token"] = token
                        # Unblock waiters now; keep reading so the pipe drains.
                        self._token_event.set()
                        continue
                if "token" in self._token_holder:
                    self.bytes_consumed_after_token += len(text)
        finally:
            self._eof_event.set()
            # Unblock waiters if the process died without printing a token.
            self._token_event.set()

    def wait_for_token(self, timeout_s: float = 60.0) -> str:
        """
        Block until the session token line is observed.

        Fail-closed if the line never arrives — without the token the
        HTML shell cannot handshake with the native authoring API.
        """
        if not self._token_event.wait(timeout_s):
            raise PackagingError(
                "timed out waiting for [ASCIIID-WEB] Session token line"
            )
        token = self._token_holder.get("token")
        if not token:
            raise PackagingError(
                "native runtime exited or never printed a session token; "
                f"captured={self.diagnostic_tail()[-2000:]!r}"
            )
        return token

    def diagnostic_tail(self) -> str:
        """Snapshot the bounded recent stdout used for receipt diagnostics."""
        with self._lock:
            return "".join(self._tail)

    def join(self, timeout: float | None = None) -> bool:
        """
        Wait for the drain thread to hit EOF.

        Returns True when the thread finished (EOF reached). Used by the
        focused drain test to prove post-token bytes were consumed.
        """
        self._thread.join(timeout)
        return not self._thread.is_alive()


def _read_token_from_stream(
    stream: Any,
    timeout_s: float = 60.0,
) -> tuple[str, str, NativeStdoutTokenDrain]:
    """
    Start a non-deadlocking stdout drain and return the session token.

    Returns (token, diagnostic_tail_snapshot, drain_handle).
    The drain_handle keeps reading through EOF after this returns.
    """
    drain = NativeStdoutTokenDrain(stream)
    drain.start()
    token = drain.wait_for_token(timeout_s=timeout_s)
    return token, drain.diagnostic_tail(), drain


def _resolve_google_chrome() -> Path:
    """Fail closed unless the Google Chrome application binary is present."""
    if not CHROME_BIN.is_file():
        raise PackagingError(
            "Google Chrome is required as the HTML product UI application "
            f"but was not found at {CHROME_BIN}. Install Google Chrome."
        )
    return CHROME_BIN


def _cfstring_to_py(cf: Any, ref: Any) -> str | None:
    """Decode one CoreFoundation CFStringRef into a Python str (UTF-8)."""
    if not ref:
        return None
    if cf.CFGetTypeID(ref) != cf.CFStringGetTypeID():
        return None
    length = int(cf.CFStringGetLength(ref))
    buf = ctypes.create_string_buffer(length * 4 + 1)
    k_utf8 = 0x08000100
    if not cf.CFStringGetCString(ref, buf, len(buf), k_utf8):
        return None
    return buf.value.decode("utf-8", errors="replace")


def list_onscreen_chrome_windows() -> list[dict[str, Any]]:
    """
    Read on-screen Google Chrome windows via CoreGraphics (ctypes).

    Returns owner/title/bounds for each Chrome window. Titles may be empty
    when the launching process lacks Screen Recording TCC — empty title is
    not "no window". Independent of AppleScript / Swift / screenshot / CDP.
    """
    cf = ctypes.cdll.LoadLibrary(
        "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
    )
    cg = ctypes.cdll.LoadLibrary(
        "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
    )
    cf_index = ctypes.c_long
    cf.CFArrayGetCount.argtypes = [ctypes.c_void_p]
    cf.CFArrayGetCount.restype = cf_index
    cf.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, cf_index]
    cf.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
    cf.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    cf.CFDictionaryGetValue.restype = ctypes.c_void_p
    cf.CFStringCreateWithCString.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32
    ]
    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    cf.CFStringGetLength.argtypes = [ctypes.c_void_p]
    cf.CFStringGetLength.restype = cf_index
    cf.CFStringGetCString.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, cf_index, ctypes.c_uint32
    ]
    cf.CFStringGetCString.restype = ctypes.c_bool
    cf.CFStringGetTypeID.restype = cf_index
    cf.CFGetTypeID.argtypes = [ctypes.c_void_p]
    cf.CFGetTypeID.restype = cf_index
    cf.CFNumberGetValue.argtypes = [ctypes.c_void_p, ctypes.c_int32, ctypes.c_void_p]
    cf.CFNumberGetValue.restype = ctypes.c_bool
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    cg.CGWindowListCopyWindowInfo.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    cg.CGWindowListCopyWindowInfo.restype = ctypes.c_void_p

    k_utf8 = 0x08000100
    k_onscreen_only = 1
    # CFNumber type for double (bounds dict values).
    k_cfnumber_double = 13
    owner_key = cf.CFStringCreateWithCString(None, b"kCGWindowOwnerName", k_utf8)
    name_key = cf.CFStringCreateWithCString(None, b"kCGWindowName", k_utf8)
    bounds_key = cf.CFStringCreateWithCString(None, b"kCGWindowBounds", k_utf8)
    width_key = cf.CFStringCreateWithCString(None, b"Width", k_utf8)
    height_key = cf.CFStringCreateWithCString(None, b"Height", k_utf8)
    arr = cg.CGWindowListCopyWindowInfo(k_onscreen_only, 0)
    windows: list[dict[str, Any]] = []

    def _cfnumber_to_float(ref: Any) -> float:
        if not ref:
            return 0.0
        value = ctypes.c_double(0.0)
        if not cf.CFNumberGetValue(ref, k_cfnumber_double, ctypes.byref(value)):
            return 0.0
        return float(value.value)

    try:
        if not arr:
            return windows
        count = int(cf.CFArrayGetCount(arr))
        for index in range(count):
            entry = cf.CFArrayGetValueAtIndex(arr, index)
            owner = _cfstring_to_py(
                cf, cf.CFDictionaryGetValue(entry, owner_key)
            )
            if not owner or "Chrome" not in owner:
                continue
            title = _cfstring_to_py(
                cf, cf.CFDictionaryGetValue(entry, name_key)
            ) or ""
            bounds_ref = cf.CFDictionaryGetValue(entry, bounds_key)
            width = 0.0
            height = 0.0
            if bounds_ref:
                width = _cfnumber_to_float(
                    cf.CFDictionaryGetValue(bounds_ref, width_key)
                )
                height = _cfnumber_to_float(
                    cf.CFDictionaryGetValue(bounds_ref, height_key)
                )
            windows.append(
                {
                    "owner": owner,
                    "title": title,
                    "width": width,
                    "height": height,
                }
            )
    finally:
        if arr:
            cf.CFRelease(arr)
        cf.CFRelease(owner_key)
        cf.CFRelease(name_key)
        cf.CFRelease(bounds_key)
        cf.CFRelease(width_key)
        cf.CFRelease(height_key)
    return windows


def list_onscreen_chrome_window_titles() -> list[str]:
    """Compatibility helper: non-empty Chrome window titles only."""
    return [
        str(window["title"])
        for window in list_onscreen_chrome_windows()
        if window.get("title")
    ]


def _window_is_forbidden_native_product(owner: str, title: str) -> bool:
    """
    Decide whether ONE on-screen window is a forbidden NATIVE product window.

    HTML-only launch rule (see module header + the docstring on
    ``_list_onscreen_forbidden_native_window_candidates``): the native ImGui
    editor ("ASCIIID Edit") and the native terminals ("GAME TERM" /
    "ASCIIID Term") must never be visible. Google Chrome is the SOLE allowed
    visible product surface — it renders the HTML shell. This title predicate
    only marks a CANDIDATE; ownership (PID) decides blocking vs. foreign in
    ``classify_forbidden_native_windows_by_pid``.

    Parameters
    ----------
    owner:
        ``kCGWindowOwnerName`` — the owning application. The native ImGui /
        TERM++ windows are drawn by the native product binaries
        (``.run/asciicker-presenter``, ``.run/asciiid-classic``), so their
        owner is that process (e.g. "asciicker-presenter"); the HTML shell window is owned
        by "Google Chrome".
    title:
        ``kCGWindowName`` — the window / page title.

    Returns
    -------
    bool
        ``True`` only for a genuine native product window.

    Why the Chrome guard is mandatory
    ---------------------------------
    Window ownership, rather than title resemblance, separates the allowed
    HTML product surface from forbidden native product windows. The shell title
    names its runtime owner, "ASCIID FL4512 Presenter", while retired ImGui and
    TERM++ titles remain forbidden. A Chrome-owned window is the HTML product
    surface (or Chrome's own UI), not a native product window. Genuine native
    windows are still caught because they are owned by a native product process
    (``asciicker-presenter`` or ``asciiid-classic``), not by the browser.
    """
    # Chrome-owned windows are the allowed HTML surface — never native product.
    if owner == UI_APPLICATION_CHROME:
        return False
    # Match the forbidden native titles against owner + title so a native
    # window is caught whether the marker lives in its owner name or its title.
    hay = f"{owner} {title}"
    return any(s in hay for s in FORBIDDEN_NATIVE_WINDOW_TITLE_SUBSTRINGS)


def _list_onscreen_forbidden_native_window_candidates() -> list[dict[str, Any]]:
    """
    Raw CoreGraphics scan for on-screen windows whose (owner, title) match the
    forbidden native product UI — the native ImGui editor ("ASCIIID Edit") and
    the native terminals ("GAME TERM" / "ASCIIID Term").

    Each candidate carries its OWNING process id (kCGWindowOwnerPID) so the
    acceptance gate can decide ownership by PID.

    IMPORTANT (FL-4588 PRODUCT-PID-WINDOW-CENSUS): this function is a DIAGNOSTIC
    COLLECTOR ONLY — it is deliberately NOT the acceptance owner. The former
    global, PID-agnostic classifier `list_onscreen_forbidden_native_windows` was
    removed as an acceptance owner because a same-title window from an UNRELATED
    repo/process (for example a `game` process on the Desktop showing
    "GAME TERM [...]") must NEVER fail THIS launched product's HTML-only gate.
    Ownership is resolved by PID in `classify_forbidden_native_windows_by_pid`;
    foreign matches are retained only as a nonblocking diagnostic.

    Returns a list of {"owner", "title", "layer", "pid"} dicts. "pid" is -1 when
    kCGWindowOwnerPID is absent/unreadable for a window.
    """
    # Reuse Chrome window enumerator infrastructure by scanning all owners.
    cf = ctypes.cdll.LoadLibrary(
        "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
    )
    cg = ctypes.cdll.LoadLibrary(
        "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
    )
    cf_index = ctypes.c_long
    cf.CFArrayGetCount.argtypes = [ctypes.c_void_p]
    cf.CFArrayGetCount.restype = cf_index
    cf.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, cf_index]
    cf.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
    cf.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    cf.CFDictionaryGetValue.restype = ctypes.c_void_p
    cf.CFStringCreateWithCString.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32
    ]
    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    cf.CFStringGetLength.argtypes = [ctypes.c_void_p]
    cf.CFStringGetLength.restype = cf_index
    cf.CFStringGetCString.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, cf_index, ctypes.c_uint32
    ]
    cf.CFStringGetCString.restype = ctypes.c_bool
    cf.CFGetTypeID.argtypes = [ctypes.c_void_p]
    cf.CFGetTypeID.restype = ctypes.c_ulong
    cf.CFStringGetTypeID.argtypes = []
    cf.CFStringGetTypeID.restype = ctypes.c_ulong
    cf.CFNumberGetValue.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    cf.CFNumberGetValue.restype = ctypes.c_bool
    cg.CGWindowListCopyWindowInfo.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    cg.CGWindowListCopyWindowInfo.restype = ctypes.c_void_p

    k_cg_window_list_option_on_screen_only = 1 << 0
    k_cg_null_window_id = 0
    info = cg.CGWindowListCopyWindowInfo(
        k_cg_window_list_option_on_screen_only, k_cg_null_window_id
    )
    if not info:
        return []
    forbidden: list[dict[str, Any]] = []
    count = int(cf.CFArrayGetCount(info))
    key_owner = cf.CFStringCreateWithCString(None, b"kCGWindowOwnerName", 0x08000100)
    key_name = cf.CFStringCreateWithCString(None, b"kCGWindowName", 0x08000100)
    key_bounds = cf.CFStringCreateWithCString(None, b"kCGWindowBounds", 0x08000100)
    key_layer = cf.CFStringCreateWithCString(None, b"kCGWindowLayer", 0x08000100)
    # kCGWindowOwnerPID lets the acceptance gate distinguish a native window
    # owned by THIS launched product from a same-title foreign process.
    key_pid = cf.CFStringCreateWithCString(None, b"kCGWindowOwnerPID", 0x08000100)
    for i in range(count):
        entry = cf.CFArrayGetValueAtIndex(info, i)
        if not entry:
            continue
        layer_ref = cf.CFDictionaryGetValue(entry, key_layer)
        layer = 0
        if layer_ref:
            layer_val = ctypes.c_int64(0)
            # kCFNumberSInt64Type = 4
            if cf.CFNumberGetValue(layer_ref, 4, ctypes.byref(layer_val)):
                layer = int(layer_val.value)
        if layer != 0:
            continue
        owner = _cfstring_to_py(cf, cf.CFDictionaryGetValue(entry, key_owner)) or ""
        title = _cfstring_to_py(cf, cf.CFDictionaryGetValue(entry, key_name)) or ""
        # FL-4588: use the pure predicate so the Chrome-shell guard (and the
        # exact forbidden-title logic) is unit-testable without live
        # CoreGraphics windows. A Chrome-owned "ASCIID FL4512 Presenter" shell
        # window is the allowed HTML surface, not a native product window.
        if not _window_is_forbidden_native_product(owner, title):
            continue
        # Read the OWNING process id (kCGWindowOwnerPID). Ownership — not the
        # title alone — decides whether this is a BLOCKING product window (owned
        # by the launched native PID) or a nonblocking FOREIGN diagnostic.
        pid = -1
        pid_ref = cf.CFDictionaryGetValue(entry, key_pid)
        if pid_ref:
            pid_val = ctypes.c_int64(0)
            # kCFNumberSInt64Type = 4 — converts any integer CFNumber to 64-bit.
            if cf.CFNumberGetValue(pid_ref, 4, ctypes.byref(pid_val)):
                pid = int(pid_val.value)
        # A native title match is only a CANDIDATE here; the PID classifier below
        # decides blocking vs. foreign-diagnostic.
        forbidden.append(
            {"owner": owner, "title": title, "layer": layer, "pid": pid}
        )
    return forbidden


def classify_forbidden_native_windows_by_pid(
    candidates: list[dict[str, Any]],
    native_pid: int | None,
) -> dict[str, Any]:
    """
    PID-scoped classification of forbidden-native-window CANDIDATES.

    Pure and unit-testable (no CoreGraphics): given the raw candidate windows
    (each carrying its owning "pid") plus the launched native product PID,
    partition them into:
      * product_owned — candidates owned by ``native_pid``. These BLOCK the
        HTML-only launch gate: the launched presenter runtime must never
        show an ImGui editor or a bare TERM++ window.
      * foreign — same-title candidates owned by ANY OTHER pid (for example a
        `game` process from an unrelated repo). These are a NONBLOCKING
        DIAGNOSTIC: another project's window must never fail this product gate.

      * unattributed — candidates whose owner PID is MISSING/UNREADABLE
        (pid <= 0), OR any candidate when ``native_pid`` itself is unknown.
        These also BLOCK: an unattributed forbidden window cannot be confirmed
        harmless, so it is fail-shut, never silently dismissed as foreign.

    ``pid_missing`` is True when ``native_pid`` is None or non-positive. The gate
    fails shut in that case: without a launched PID we cannot confirm the product
    is HTML-only, so we refuse rather than pass blind.
    """
    pid_missing = native_pid is None or int(native_pid) <= 0
    product_owned: list[dict[str, Any]] = []
    foreign: list[dict[str, Any]] = []
    unattributed: list[dict[str, Any]] = []
    for window in candidates:
        window_pid = int(window.get("pid", -1))
        if not pid_missing and window_pid == int(native_pid):
            # Proven to belong to THIS launched product -> blocking.
            product_owned.append(window)
        elif not pid_missing and window_pid > 0:
            # A POSITIVE PID known to DIFFER from native_pid -> foreign
            # (nonblocking): another project's window, never our gate's concern.
            foreign.append(window)
        else:
            # Owner PID missing/unreadable (window_pid <= 0), or the launched
            # native PID itself is unknown. We CANNOT show this window is a
            # harmless foreign process, so it is UNATTRIBUTED and BLOCKS.
            unattributed.append(window)
    return {
        "native_pid": None if native_pid is None else int(native_pid),
        "pid_missing": pid_missing,
        "product_owned": product_owned,
        "unattributed": unattributed,
        "foreign": foreign,
    }


def observe_zero_visible_native_product_windows(
    native_pid: int | None,
) -> dict[str, Any]:
    """
    Fail-closed, PID-scoped observation: zero visible ImGui and bare TERM++
    windows owned by the launched native product.
    """
    candidates = _list_onscreen_forbidden_native_window_candidates()
    classified = classify_forbidden_native_windows_by_pid(candidates, native_pid)
    product_owned = classified["product_owned"]
    unattributed = classified["unattributed"]
    foreign = classified["foreign"]
    pid_missing = classified["pid_missing"]
    if pid_missing:
        blocker = (
            "launched native product PID unavailable — cannot confirm "
            "HTML-only visibility (fail shut)"
        )
    elif product_owned:
        blocker = (
            "visible native product window(s) owned by launched PID "
            f"{classified['native_pid']} forbidden under HTML-only launch: "
            f"{product_owned!r}"
        )
    elif unattributed:
        blocker = (
            "unattributed forbidden native window(s) (owner PID "
            "missing/unreadable) — blocking under HTML-only launch: "
            f"{unattributed!r}"
        )
    else:
        blocker = None
    return {
        "observed_native_pid": classified["native_pid"],
        "visible_native_product_window_count": len(product_owned),
        "visible_native_product_windows": product_owned,
        "unattributed_forbidden_native_window_count": len(unattributed),
        "unattributed_forbidden_native_windows": unattributed,
        "visible_native_product_windows_required": 0,
        "native_product_windows_absent_verified": (
            not pid_missing
            and len(product_owned) == 0
            and len(unattributed) == 0
        ),
        "native_product_windows_blocker": blocker,
        "foreign_forbidden_native_windows": foreign,
        "foreign_forbidden_native_window_count": len(foreign),
    }


def chrome_authoring_tcp_established(*, port: int = AUTHORING_PORT) -> dict[str, Any]:
    """
    Observe whether Google Chrome holds an ESTABLISHED TCP session to the
    native authoring port. The HTML shell (served on SHELL_PORT) opens that
    session after load — so this is shell-loaded evidence without CDP.
    """
    completed = subprocess.run(
        ["lsof", f"-iTCP:{port}", "-sTCP:ESTABLISHED", "-n", "-P"],
        capture_output=True,
        text=True,
        check=False,
    )
    lines = [
        line for line in (completed.stdout or "").splitlines()
        if line and not line.startswith("COMMAND")
    ]
    chrome_lines = [
        line for line in lines
        if "Google" in line or "Chrome" in line
    ]
    return {
        "authoring_port": port,
        "chrome_authoring_tcp_established": bool(chrome_lines),
        "chrome_authoring_tcp_lines": chrome_lines[:8],
        "lsof_exit": completed.returncode,
    }


def observe_chrome_shell_visibility(
    *,
    timeout_s: float = CHROME_VISIBILITY_TIMEOUT_S,
    poll_s: float = 0.5,
) -> dict[str, Any]:
    """
    Poll until a visible Chrome window belongs to the HTML shell.

    Visual-only paths (first match wins):
    1. On-screen Chrome window title contains the shell page name.
    2. On-screen Chrome window with real bounds AND Chrome has an
       ESTABLISHED TCP session to the authoring port.
       This covers TCC-redacted window titles where title strings are empty
       but Chrome is visibly open and talking to the native runtime.

    This observer never owns HTML readiness. A title, bounds, and a TCP sample
    cannot establish that React has a live authoring session plus snapshot.
    `observe_chrome_authoring_ready_cdp` is the sole headed readiness owner.
    """
    deadline = time.time() + timeout_s
    last_windows: list[dict[str, Any]] = []
    last_tcp: dict[str, Any] = {}
    while time.time() < deadline:
        last_windows = list_onscreen_chrome_windows()
        titles = [str(w.get("title") or "") for w in last_windows]
        matched_titles = [
            title for title in titles
            if title and CHROME_SHELL_WINDOW_TITLE_SUBSTRING in title
        ]
        if matched_titles:
            return {
                "browser_visibility_verified": True,
                "html_editor_loaded_verified": False,
                "chrome_shell_window_titles": matched_titles,
                "chrome_onscreen_window_count": len(last_windows),
                "chrome_visibility_method": "coregraphics_onscreen_window_title",
                "chrome_visibility_blocker": None,
            }
        # Visible Chrome window (bounds) even when title is TCC-redacted.
        visible_chrome = [
            w for w in last_windows
            if float(w.get("width") or 0) >= 200 and float(w.get("height") or 0) >= 200
        ]
        last_tcp = chrome_authoring_tcp_established()
        if visible_chrome and last_tcp.get("chrome_authoring_tcp_established"):
            return {
                "browser_visibility_verified": True,
                "html_editor_loaded_verified": False,
                "chrome_shell_window_titles": [
                    str(w.get("title") or "") for w in visible_chrome
                ],
                "chrome_onscreen_window_count": len(visible_chrome),
                "chrome_authoring_tcp_established": True,
                "chrome_visibility_method": (
                    "coregraphics_onscreen_chrome_bounds+"
                    "lsof_authoring_tcp_established"
                ),
                "chrome_visibility_blocker": None,
                "chrome_visibility_note": (
                    "window titles were empty/redacted; verified via on-screen "
                    "Chrome bounds plus ESTABLISHED TCP to authoring port "
                    f"{AUTHORING_PORT} (HTML shell transport)"
                ),
            }
        time.sleep(poll_s)
    title_list = [str(w.get("title") or "") for w in last_windows if w.get("title")]
    return {
        "browser_visibility_verified": False,
        "html_editor_loaded_verified": False,
        "chrome_shell_window_titles": title_list,
        "chrome_onscreen_window_count": len(last_windows),
        "chrome_authoring_tcp_established": bool(
            last_tcp.get("chrome_authoring_tcp_established")
        ),
        "chrome_visibility_method": (
            "coregraphics_onscreen_window_title|"
            "coregraphics_bounds+lsof_authoring_tcp"
        ),
        "chrome_visibility_blocker": (
            "no on-screen Google Chrome shell evidence within "
            f"{timeout_s:.0f}s (title match or bounds+authoring-TCP); "
            f"windows={len(last_windows)} titled={title_list!r} "
            f"tcp={bool(last_tcp.get('chrome_authoring_tcp_established'))}"
        ),
    }


class _ChromeCdpPage:
    """Small read-only Chrome DevTools page client using Python stdlib only."""

    _WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

    def __init__(self, websocket_url: str, *, timeout_s: float = 3.0) -> None:
        parsed = urlparse(websocket_url)
        if (
            parsed.scheme != "ws"
            or (parsed.hostname or "").lower() not in ("127.0.0.1", "::1")
            or not isinstance(parsed.port, int)
        ):
            raise PackagingError(
                f"Chrome page DevTools websocket is not loopback ws: {websocket_url!r}"
            )
        self._socket = socket.create_connection(
            (parsed.hostname, parsed.port), timeout=timeout_s
        )
        self._socket.settimeout(timeout_s)
        self._buffer = b""
        self._next_id = 1
        self._events: list[dict[str, Any]] = []
        path = parsed.path or "/"
        if parsed.query:
            path += f"?{parsed.query}"
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {parsed.hostname}:{parsed.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).encode("ascii")
        self._socket.sendall(request)
        response = self._read_until(b"\r\n\r\n", limit=65536)
        header_bytes, self._buffer = response.split(b"\r\n\r\n", 1)
        lines = header_bytes.decode("iso-8859-1").split("\r\n")
        if not lines or " 101 " not in f" {lines[0]} ":
            self.close()
            raise PackagingError(
                f"Chrome page DevTools websocket upgrade failed: {lines[0] if lines else 'empty'}"
            )
        headers: dict[str, str] = {}
        for line in lines[1:]:
            name, separator, value = line.partition(":")
            if separator:
                headers[name.strip().lower()] = value.strip()
        expected_accept = base64.b64encode(
            hashlib.sha1((key + self._WEBSOCKET_GUID).encode("ascii")).digest()
        ).decode("ascii")
        if headers.get("sec-websocket-accept") != expected_accept:
            self.close()
            raise PackagingError("Chrome page DevTools websocket accept mismatch")

    def _read_until(self, marker: bytes, *, limit: int) -> bytes:
        data = self._buffer
        while marker not in data:
            chunk = self._socket.recv(65536)
            if not chunk:
                raise PackagingError("Chrome page DevTools websocket closed during handshake")
            data += chunk
            if len(data) > limit:
                raise PackagingError("Chrome page DevTools websocket handshake too large")
        return data

    def _read_exact(self, size: int) -> bytes:
        while len(self._buffer) < size:
            chunk = self._socket.recv(max(4096, size - len(self._buffer)))
            if not chunk:
                raise PackagingError("Chrome page DevTools websocket closed")
            self._buffer += chunk
        result, self._buffer = self._buffer[:size], self._buffer[size:]
        return result

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        first = 0x80 | opcode
        length = len(payload)
        if length < 126:
            header = bytes((first, 0x80 | length))
        elif length <= 0xFFFF:
            header = bytes((first, 0x80 | 126)) + struct.pack("!H", length)
        else:
            header = bytes((first, 0x80 | 127)) + struct.pack("!Q", length)
        mask = os.urandom(4)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self._socket.sendall(header + mask + masked)

    def _receive_text(self) -> str:
        fragments: list[bytes] = []
        text_started = False
        while True:
            first, second = self._read_exact(2)
            final = bool(first & 0x80)
            opcode = first & 0x0F
            masked = bool(second & 0x80)
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read_exact(8))[0]
            mask = self._read_exact(4) if masked else b""
            payload = self._read_exact(length)
            if masked:
                payload = bytes(
                    byte ^ mask[index % 4] for index, byte in enumerate(payload)
                )
            if opcode == 0x8:
                raise PackagingError("Chrome page DevTools websocket closed")
            if opcode == 0x9:
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode == 0x1:
                fragments = [payload]
                text_started = True
            elif opcode == 0x0 and text_started:
                fragments.append(payload)
            else:
                continue
            if final:
                return b"".join(fragments).decode("utf-8")

    def command(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        command_id = self._next_id
        self._next_id += 1
        message = {"id": command_id, "method": method}
        if params is not None:
            message["params"] = params
        self._send_frame(0x1, json.dumps(message, separators=(",", ":")).encode())
        while True:
            payload = json.loads(self._receive_text())
            if not isinstance(payload, dict):
                continue
            if payload.get("id") == command_id:
                if "error" in payload:
                    raise PackagingError(
                        f"Chrome DevTools {method} failed: {payload['error']!r}"
                    )
                result = payload.get("result")
                return result if isinstance(result, dict) else {}
            if isinstance(payload.get("method"), str):
                self._events.append(payload)

    def pop_events(self) -> list[dict[str, Any]]:
        events, self._events = self._events, []
        return events

    def close(self) -> None:
        sock = getattr(self, "_socket", None)
        if sock is None:
            return
        try:
            self._send_frame(0x8, b"")
        except Exception:
            pass
        try:
            sock.close()
        finally:
            self._socket = None


def _chrome_shell_page_target(
    *,
    shell_port: int,
    urlopen: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    open_url = urlopen if urlopen is not None else urllib.request.urlopen
    with open_url(
        f"http://127.0.0.1:{CHROME_DEVTOOLS_PORT}/json/list", timeout=1.0
    ) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list):
        raise PackagingError("Chrome DevTools page target list is not an array")
    for target in payload:
        if not isinstance(target, dict) or target.get("type") != "page":
            continue
        target_url = target.get("url")
        websocket_url = target.get("webSocketDebuggerUrl")
        if not isinstance(target_url, str) or not isinstance(websocket_url, str):
            continue
        parsed = urlparse(target_url)
        if (
            parsed.scheme == "http"
            and parsed.hostname == "127.0.0.1"
            and parsed.port == shell_port
            and _chrome_devtools_websocket_is_loopback(websocket_url)
        ):
            return target
    raise PackagingError("Chrome DevTools has no page target for the packaged shell")


def _sanitize_launcher_diagnostic_text(value: Any, *, limit: int) -> str:
    """Bound and redact browser text before it can enter a launch receipt."""
    text = " ".join(str(value or "").split())
    text = re.sub(
        r"(?i)\b(x-asciiid-session|authorization|bootstrap[_ -]?token|"
        r"session[_ -]?id|session|token)\b\s*[:=]\s*[^\s,;]+",
        r"\1=<redacted>",
        text,
    )
    text = re.sub(
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",
        "<redacted>",
        text,
    )
    text = re.sub(r"\b[0-9a-fA-F]{32,}\b", "<redacted>", text)
    return text[:limit]


def _empty_launcher_http_facts() -> dict[str, dict[str, Any]]:
    return {
        name: {
            "path": path,
            "observed": False,
            "status": None,
            "response_count": 0,
        }
        for path, name in LAUNCHER_READINESS_HTTP_PATHS.items()
    }


def _record_launcher_http_fact(
    facts: dict[str, dict[str, Any]],
    *,
    path: str,
    status: Any,
) -> None:
    name = LAUNCHER_READINESS_HTTP_PATHS.get(path)
    if name is None:
        return
    fact = facts[name]
    fact["observed"] = True
    fact["response_count"] = int(fact["response_count"]) + 1
    if isinstance(status, (int, float)) and int(status) > 0:
        fact["status"] = int(status)


def _append_launcher_error_summary(
    summaries: list[str],
    value: Any,
) -> None:
    summary = _sanitize_launcher_diagnostic_text(
        value,
        limit=LAUNCHER_DIAGNOSTIC_TEXT_LIMIT,
    )
    if (
        summary
        and summary not in summaries
        and len(summaries) < LAUNCHER_DIAGNOSTIC_ERROR_LIMIT
    ):
        summaries.append(summary)


def observe_chrome_authoring_ready_cdp(
    *,
    shell_port: int = SHELL_PORT,
    timeout_s: float = CHROME_AUTHORING_READY_TIMEOUT_S,
    poll_s: float = CHROME_AUTHORING_READY_POLL_S,
    target_loader: Callable[..., dict[str, Any]] | None = None,
    client_factory: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Own headed readiness through read-only CDP observation of the shell.

    React exposes `.app-shell` only after registry identity, native health, the
    authoring session, and one atomic `asciiid.runtime_snapshot.v1` are present.
    This observer additionally watches the authenticated runtime snapshot in
    CDP Network events and requires the real pipeline navigation button. It
    never performs a UI action. Credentials, session ids, headers, request
    bodies, and response bodies are excluded from its receipt facts.
    """
    load_target = target_loader if target_loader is not None else _chrome_shell_page_target
    make_client = client_factory if client_factory is not None else _ChromeCdpPage
    deadline = time.monotonic() + timeout_s
    last_error = "cdp_authoring_readiness_not_observed"
    last_dom: dict[str, Any] = {}
    client = None
    started = time.monotonic()
    requests: dict[str, dict[str, Any]] = {}
    checked_responses: set[str] = set()
    snapshot_facts: dict[str, Any] = {}
    http_facts = _empty_launcher_http_facts()
    runtime_exceptions: list[str] = []
    console_errors: list[str] = []
    try:
        target = load_target(shell_port=shell_port)
        websocket_url = target.get("webSocketDebuggerUrl")
        if not isinstance(websocket_url, str):
            raise PackagingError("Chrome shell page target lacks websocket URL")
        client = make_client(websocket_url, timeout_s=min(3.0, timeout_s))
        client.command("Network.enable")
        client.command("Runtime.enable")
        while time.monotonic() < deadline:
            evaluated = client.command(
                "Runtime.evaluate",
                {
                    "expression": """(() => {
                      const body = document.body?.textContent || '';
                      const navigation = [...document.querySelectorAll('button[aria-label^="Open /"]')]
                        .map((node) => node.getAttribute('aria-label'));
                      const status = document.querySelector('.statusbar')?.textContent || '';
                      const diagnosticPaths = new Set([
                        '/asciiid.ui_registry.v1.json',
                        '/asciiid.authoring_bootstrap.v1.json',
                        '/api/v1/health',
                        '/api/v1/handshake',
                        '/api/v1/runtime-snapshot'
                      ]);
                      const resource_http = performance.getEntriesByType('resource')
                        .map((entry) => {
                          try {
                            const path = new URL(entry.name, location.href).pathname;
                            return diagnosticPaths.has(path)
                              ? {path, status: Number(entry.responseStatus) || null}
                              : null;
                          } catch (_) {
                            return null;
                          }
                        })
                        .filter(Boolean);
                      return {
                        schema: 'asciiid.shell_ready_dom.v1',
                        document_title: document.title,
                        document_ready_state: document.readyState,
                        body_text_summary: body.replace(/\\s+/g, ' ').trim().slice(0, 512),
                        app_shell_present: !!document.querySelector('.app-shell'),
                        starting_visible: body.includes('Starting ASCIID FL4512 Presenter'),
                        navigation_count: navigation.length,
                        navigation_present: navigation.length > 0,
                        pipeline_navigation_present: navigation.includes('Open /inspect/pipeline'),
                        live_badge_present: body.includes('ASCIID FL4512 PRESENTER · LIVE'),
                        snapshot_ticker_present: /editor frame \\d+/.test(status),
                        resource_http
                      };
                    })()""",
                    "returnByValue": True,
                },
            )
            remote = evaluated.get("result")
            value = remote.get("value") if isinstance(remote, dict) else None
            if isinstance(value, dict):
                resource_http = value.get("resource_http")
                if isinstance(resource_http, list):
                    for fact in resource_http:
                        if not isinstance(fact, dict):
                            continue
                        path = str(fact.get("path") or "")
                        name = LAUNCHER_READINESS_HTTP_PATHS.get(path)
                        if name is None or http_facts[name]["observed"] is True:
                            continue
                        _record_launcher_http_fact(
                            http_facts,
                            path=path,
                            status=fact.get("status"),
                        )
                last_dom = {
                    "schema": value.get("schema"),
                    "document_title": _sanitize_launcher_diagnostic_text(
                        value.get("document_title"),
                        limit=LAUNCHER_DIAGNOSTIC_TEXT_LIMIT,
                    ),
                    "document_ready_state": value.get("document_ready_state"),
                    "body_text_summary": _sanitize_launcher_diagnostic_text(
                        value.get("body_text_summary"),
                        limit=LAUNCHER_DIAGNOSTIC_TEXT_LIMIT,
                    ),
                    "app_shell_present": value.get("app_shell_present") is True,
                    "starting_visible": value.get("starting_visible") is True,
                    "navigation_count": value.get("navigation_count"),
                    "navigation_present": (
                        value.get("navigation_present") is True
                        or value.get("pipeline_navigation_present") is True
                        or (
                            isinstance(value.get("navigation_count"), int)
                            and value.get("navigation_count") > 0
                        )
                    ),
                    "pipeline_navigation_present": (
                        value.get("pipeline_navigation_present") is True
                    ),
                    "live_badge_present": value.get("live_badge_present") is True,
                    "snapshot_ticker_present": (
                        value.get("snapshot_ticker_present") is True
                    ),
                }

            finished_response_ids: list[str] = []
            for event in client.pop_events():
                method = event.get("method")
                params = event.get("params")
                if not isinstance(params, dict):
                    continue
                if method == "Runtime.exceptionThrown":
                    details = params.get("exceptionDetails")
                    if isinstance(details, dict):
                        exception = details.get("exception")
                        description = (
                            exception.get("description")
                            if isinstance(exception, dict)
                            else None
                        )
                        _append_launcher_error_summary(
                            runtime_exceptions,
                            description or details.get("text"),
                        )
                    continue
                if method == "Runtime.consoleAPICalled":
                    if params.get("type") == "error":
                        parts: list[str] = []
                        args = params.get("args")
                        if isinstance(args, list):
                            for arg in args[:4]:
                                if not isinstance(arg, dict):
                                    continue
                                part = arg.get("value")
                                if part is None:
                                    part = arg.get("description")
                                if part is not None:
                                    parts.append(str(part))
                        _append_launcher_error_summary(
                            console_errors,
                            " ".join(parts) or "console.error",
                        )
                    continue
                request_id = params.get("requestId")
                if not isinstance(request_id, str):
                    continue
                if method == "Network.requestWillBeSent":
                    request = params.get("request")
                    if not isinstance(request, dict):
                        continue
                    request_url = str(request.get("url") or "")
                    if request_url.endswith("/api/v1/runtime-snapshot"):
                        headers = request.get("headers")
                        session_value = None
                        if isinstance(headers, dict):
                            for name, header_value in headers.items():
                                if str(name).lower() == "x-asciiid-session":
                                    session_value = str(header_value)
                                    break
                        requests[request_id] = {
                            "session_header_present": bool(session_value),
                        }
                elif method == "Network.responseReceived":
                    response = params.get("response")
                    if not isinstance(response, dict):
                        continue
                    response_url = str(response.get("url") or "")
                    _record_launcher_http_fact(
                        http_facts,
                        path=urlparse(response_url).path,
                        status=response.get("status"),
                    )
                    if (
                        response_url.endswith("/api/v1/runtime-snapshot")
                        and request_id in requests
                    ):
                        requests[request_id]["response_status"] = int(
                            response.get("status") or 0
                        )
                elif (
                    method == "Network.loadingFinished"
                    and request_id in requests
                    and requests[request_id].get("response_status") == 200
                    and request_id not in checked_responses
                ):
                    finished_response_ids.append(request_id)

            for request_id in finished_response_ids:
                checked_responses.add(request_id)
                body_result = client.command(
                    "Network.getResponseBody", {"requestId": request_id}
                )
                body_text = body_result.get("body")
                if not isinstance(body_text, str):
                    continue
                if body_result.get("base64Encoded") is True:
                    body_text = base64.b64decode(body_text).decode("utf-8")
                body = json.loads(body_text)
                if not isinstance(body, dict):
                    continue
                snapshot = body.get("state")
                witness = body.get("witness")
                request_facts = requests.get(request_id, {})
                if (
                    body.get("schema") == "asciiid.runtime_snapshot.v1"
                    and isinstance(snapshot, dict)
                    and snapshot.get("schema") == "asciiid.authoring_snapshot.v1"
                    and isinstance(witness, dict)
                    and witness.get("schema") == "asciiid.runtime_witness.v1"
                    and request_facts.get("session_header_present") is True
                ):
                    snapshot_facts = {
                        "shell_live_authoring_session_verified": True,
                        "shell_live_snapshot_verified": True,
                        "shell_runtime_snapshot_schema": body.get("schema"),
                        "shell_runtime_witness_schema": witness.get("schema"),
                        "shell_snapshot_schema": snapshot.get("schema"),
                        "shell_snapshot_state_revision": snapshot.get("state_revision"),
                        "shell_snapshot_world_generation": (
                            (snapshot.get("runtime") or {}).get("world") or {}
                        ).get("generation"),
                    }

            dom_ready = (
                last_dom.get("app_shell_present") is True
                and last_dom.get("starting_visible") is False
                and last_dom.get("navigation_present") is True
                and last_dom.get("live_badge_present") is True
                and last_dom.get("snapshot_ticker_present") is True
            )
            presenter_identity_verified = (
                last_dom.get("document_title")
                == CHROME_SHELL_WINDOW_TITLE_SUBSTRING
                and last_dom.get("live_badge_present") is True
            )
            if dom_ready and snapshot_facts.get("shell_live_snapshot_verified") is True:
                return {
                    "html_editor_loaded_verified": True,
                    "shell_ready_via_cdp": True,
                    "shell_real_navigation_verified": True,
                    "shell_ready_observer": "launcher_cdp_network_plus_dom.v1",
                    "shell_ready_elapsed_s": round(time.monotonic() - started, 3),
                    "shell_ready_dom": last_dom,
                    "shell_ready_http_facts": http_facts,
                    "shell_ready_runtime_exceptions": runtime_exceptions,
                    "shell_ready_console_errors": console_errors,
                    "shell_ready_blocker": None,
                    "presenter_identity_verified": presenter_identity_verified,
                    **snapshot_facts,
                }
            last_error = (
                "shell_dom_not_ready" if not dom_ready else "live_snapshot_not_observed"
            )
            time.sleep(poll_s)
    except Exception as exc:
        last_error = f"{type(exc).__name__}:{exc}"
    finally:
        if client is not None:
            client.close()
    return {
        "html_editor_loaded_verified": False,
        "shell_ready_via_cdp": False,
        "shell_real_navigation_verified": False,
        "shell_live_authoring_session_verified": False,
        "shell_live_snapshot_verified": False,
        "shell_ready_observer": "launcher_cdp_network_plus_dom.v1",
        "shell_ready_elapsed_s": round(time.monotonic() - started, 3),
        "shell_ready_dom": last_dom,
        "shell_ready_http_facts": http_facts,
        "shell_ready_runtime_exceptions": runtime_exceptions,
        "shell_ready_console_errors": console_errors,
        "shell_ready_blocker": last_error,
        "presenter_identity_verified": (
            last_dom.get("document_title")
            == CHROME_SHELL_WINDOW_TITLE_SUBSTRING
            and last_dom.get("live_badge_present") is True
        ),
        **snapshot_facts,
    }


def observe_chrome_shell_lifecycle(
    *,
    helper_pid: int,
    shell_port: int,
) -> dict[str, Any]:
    """Observe the exact owned Chrome process plus its HTML editor page target."""
    command = _ps_command_for_pid(helper_pid)
    app_prefix = f"--app=http://127.0.0.1:{shell_port}/"
    helper_owned = bool(
        command
        and str(CHROME_BIN) in command
        and app_prefix in command
        and "--user-data-dir=" in command
    )
    target_urls: list[str] = []
    target_error: str | None = None
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{CHROME_DEVTOOLS_PORT}/json/list",
            timeout=0.75,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, list):
            raise ValueError("DevTools target list is not an array")
        target_urls = [
            str(target.get("url"))
            for target in payload
            if isinstance(target, dict)
            and target.get("type") == "page"
            and isinstance(target.get("url"), str)
        ]
    except (OSError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        target_error = f"{type(exc).__name__}:{exc}"

    target_present = False
    for target_url in target_urls:
        parsed = urlparse(target_url)
        if (
            parsed.scheme == "http"
            and parsed.hostname == "127.0.0.1"
            and parsed.port == shell_port
        ):
            target_present = True
            break
    return {
        # DELETE FIRST (FL-4588): sampled CoreGraphics visibility plus a
        # momentary authoring TCP connection no longer owns application
        # lifetime. Initial visibility is verified once before this function.
        # Continued lifetime belongs to the exact isolated Chrome command and
        # its exact loopback HTML page target.
        "chrome_shell_lifecycle_present": helper_owned and target_present,
        "chrome_shell_helper_pid": helper_pid,
        "chrome_shell_helper_owned": helper_owned,
        "chrome_shell_target_present": target_present,
        "chrome_shell_target_urls": target_urls,
        "chrome_shell_target_error": target_error,
    }


def wait_for_product_lifecycle(
    native: subprocess.Popen[str],
    *,
    open_browser: bool,
    chrome_helper_pid: int | None = None,
    shell_port: int = SHELL_PORT,
    missing_shell_samples_required: int = 4,
    poll_s: float = 0.5,
) -> dict[str, Any]:
    """Wait until backend exit, launcher interrupt, then headed shell close."""
    missing_shell_samples = 0
    last_shell_facts: dict[str, Any] = {}
    while native.poll() is None:
        if open_browser:
            if not isinstance(chrome_helper_pid, int) or chrome_helper_pid <= 1:
                raise PackagingError(
                    "headed lifecycle requires the owned Chrome helper PID"
                )
            last_shell_facts = observe_chrome_shell_lifecycle(
                helper_pid=chrome_helper_pid,
                shell_port=shell_port,
            )
            shell_present = (
                last_shell_facts.get("chrome_shell_lifecycle_present") is True
            )
            if shell_present:
                missing_shell_samples = 0
            else:
                missing_shell_samples += 1
                if missing_shell_samples >= missing_shell_samples_required:
                    # FL-4588: Chrome shell close owns backend teardown. Do not
                    # leave the hidden native runtime running after the only
                    # visible product UI is gone.
                    if native.poll() is None:
                        native.send_signal(signal.SIGTERM)
                        try:
                            native.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            native.kill()
                            native.wait(timeout=5)
                    return {
                        "lifecycle_exit_reason": "chrome_shell_closed",
                        "chrome_shell_missing_samples": missing_shell_samples,
                        "native_backend_teardown_on_shell_close": True,
                        "native_backend_exit_code": native.poll(),
                        **last_shell_facts,
                    }
        time.sleep(poll_s)
    return {
        "lifecycle_exit_reason": "native_backend_exited",
        "native_backend_exit_code": native.poll(),
        "chrome_shell_missing_samples": missing_shell_samples,
        **last_shell_facts,
    }



def _unproven_headed_launch_facts(
    *,
    ui_launch_requested: bool,
    launchservices_request_accepted: bool,
) -> dict[str, Any]:
    """
    Boundary fields for a Chrome launch request.

    Spawning Chrome / activating the app is NOT a visible-browser
    observation. Independent headed inspection must set verified_* later.
    """
    return {
        "ui_launch_requested": ui_launch_requested,
        "launchservices_request_accepted": launchservices_request_accepted,
        "browser_visibility_verified": False,
        "html_editor_loaded_verified": False,
        "physical_user_action_verified": False,
        "proof_status": PROOF_STATUS_HEADED_LAUNCH_PENDING,
    }


def _chrome_devtools_websocket_is_loopback(ws_url: str) -> bool:
    """
    Return True only when webSocketDebuggerUrl targets loopback IP.

    DELETED ASSUMPTION: remote-debugging flags on the Chrome argv do not
    mean DevTools is reachable. The readiness gate must observe
    /json/version and reject websocket URLs bound to a non-loopback host
    (LAN IP, 0.0.0.0 advertisement, etc.).
    """
    parsed = urlparse(ws_url)
    if parsed.scheme not in ("ws", "wss"):
        return False
    host = (parsed.hostname or "").lower()
    # Match --remote-debugging-address=127.0.0.1. Reject hostname aliases
    # and any routable interface so attach stays on the owned loopback port.
    return host in ("127.0.0.1", "::1")


def wait_chrome_devtools_ready(
    *,
    timeout_s: float = CHROME_DEVTOOLS_READY_TIMEOUT_S,
    poll_s: float = CHROME_DEVTOOLS_READY_POLL_S,
    version_url: str = CHROME_DEVTOOLS_VERSION_URL,
    urlopen: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """
    Poll Chrome's loopback /json/version until attach is readiness-confirmed.

    Success requires a JSON object with webSocketDebuggerUrl whose host is
    loopback (127.0.0.1 or ::1). Timeout, HTTP failure, malformed JSON, a
    missing websocket field, or a non-loopback websocket all fail closed.
    Tests inject urlopen so no browser process is launched.
    """
    open_url = urlopen if urlopen is not None else urllib.request.urlopen
    deadline = time.time() + timeout_s
    polls = 0
    last_error = "devtools_not_probed"
    started = time.time()
    while time.time() < deadline:
        polls += 1
        try:
            with open_url(
                version_url,
                timeout=min(2.0, max(0.05, deadline - time.time())),
            ) as response:
                raw = response.read()
            payload = json.loads(raw.decode("utf-8"))
        except urllib.error.URLError as exc:
            last_error = f"devtools_http_unreachable:{exc.reason!r}"
            time.sleep(poll_s)
            continue
        except TimeoutError as exc:
            last_error = f"devtools_http_timeout:{exc}"
            time.sleep(poll_s)
            continue
        except json.JSONDecodeError as exc:
            last_error = f"devtools_json_malformed:{exc}"
            time.sleep(poll_s)
            continue
        except Exception as exc:  # noqa: BLE001 — bounded readiness probe
            last_error = f"devtools_probe_error:{type(exc).__name__}:{exc}"
            time.sleep(poll_s)
            continue

        if not isinstance(payload, dict):
            last_error = "devtools_payload_not_object"
            time.sleep(poll_s)
            continue
        ws_url = payload.get("webSocketDebuggerUrl")
        if not isinstance(ws_url, str) or not ws_url.strip():
            last_error = "devtools_websocket_debugger_url_missing"
            time.sleep(poll_s)
            continue
        if not _chrome_devtools_websocket_is_loopback(ws_url):
            # Wrong-host is a hard fail — do not keep polling a LAN bind.
            raise PackagingError(
                "Chrome DevTools websocket debugger URL is not loopback: "
                f"{ws_url!r}"
            )
        elapsed = time.time() - started
        browser = payload.get("Browser")
        protocol = payload.get("Protocol-Version")
        return {
            "chrome_devtools_ready": True,
            "chrome_devtools_version_url": version_url,
            "chrome_devtools_browser": browser if isinstance(browser, str) else None,
            "chrome_devtools_protocol_version": (
                protocol if isinstance(protocol, str) else None
            ),
            "chrome_devtools_websocket_debugger_url": ws_url,
            "chrome_devtools_ready_elapsed_s": round(elapsed, 3),
            "chrome_devtools_ready_polls": polls,
            "chrome_devtools_ready_error": None,
        }
    raise PackagingError(
        "Chrome DevTools attach endpoint unreachable after launch "
        f"(url={version_url}, timeout_s={timeout_s}, polls={polls}, "
        f"last_error={last_error})"
    )


def _open_editor_in_chrome(editor_url: str) -> dict[str, Any]:
    """
    Open exactly one Chrome app-mode window for the HTML shell.

    Defect history (hard-deleted owners):
    - `open -a Google Chrome <url>` reuses a background tab and often
      never surfaces a visible product window.
    - `--new-window <url>` is a generic browser window, not the owned
      single-product app-mode shell.
    - Leaving that window open after backend death presents a
      disconnected shell as the editor.
    - Assuming `--remote-debugging-port` alone means DevTools is
      reachable (deleted: readiness now polls /json/version).

    Owned path: invoke the Chrome binary with `--app=<editor_url>` so
    the shell is one dedicated app-mode window, then confirm the loopback
    DevTools HTTP endpoint answers with a loopback websocket URL.
    Spawning is a launch *request* only. verified_* fields stay false
    until CoreGraphics sees the HTML shell and zero forbidden native
    product windows. Receipts never treat the spawn itself as
    physical-action success.
    """
    chrome = _resolve_google_chrome()
    # DELETE FIRST (FL-4588 PRELAUNCH-SINGLE-SESSION): the late stale-Chrome
    # retirement that used to run HERE — after the native backend had already
    # started — is removed. A prior session's Chrome shell is now retired exactly
    # once, BEFORE the native launch, by the sole pre-native lifecycle owner
    # retire_prior_single_session_before_native(). Retiring Chrome after the new
    # backend is up races the new shell and is no longer an owner at this seam.
    # Chrome ignores remote-debugging flags when a normal profile is already
    # owned by another process. The editor shell therefore gets one ephemeral
    # profile tied to this launcher lifecycle. This still produces exactly one
    # product window and gives the read-only coordinator one attach port.
    profile_dir = Path(tempfile.mkdtemp(prefix="asciiid-html-chrome-profile-"))
    # Detach: cold-start Chrome must not be joined by the launcher.
    try:
        helper = subprocess.Popen(
            [
                str(chrome),
                f"--app={editor_url}",
                "--remote-debugging-address=127.0.0.1",
                f"--remote-debugging-port={CHROME_DEVTOOLS_PORT}",
                f"--user-data-dir={profile_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                # FL-4652 launcher liveness (guard-hook mandate: fix it in the
                # launcher, never with window-focus choreography). When the
                # operator's terminal is a full-screen app it owns the active
                # macOS Space, so the --app shell opens on a BACKGROUND Space.
                # macOS then reports the window occluded and Chrome stops laying
                # out the page ("environment-freeze navigation is not visible").
                #
                # Two coupled facts made this hard: (a) the anti-occlusion +
                # anti-throttle flags that keep a background-Space page fully
                # rendered ALSO keep the renderer hot, and with the retina-scaled
                # ~2560x1440 TERM++ canvas that drove the renderer to "Page
                # crashed"; (b) the single occlusion flag alone was too weak to
                # keep layout reliable. The fix is to SHRINK the canvas: a small
                # window at device-scale 1 makes the shell light enough that the
                # full anti-occlusion + anti-throttle set is survivable, so the
                # page stays laid out on a background Space without crashing. The
                # ASCII cell grid is driven by the term++ dimensions, not the
                # window pixel size, so the exact-cell proof is unaffected.
                "--window-size=1024,720",
                "--force-device-scale-factor=1",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                "--disable-background-timer-throttling",
                "--disable-features=CalculateNativeWinOcclusion",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        shutil.rmtree(profile_dir, ignore_errors=True)
        raise
    if helper.pid <= 0:
        shutil.rmtree(profile_dir, ignore_errors=True)
        raise PackagingError(
            "failed to spawn Google Chrome --app= for the "
            f"editor URL (pid={helper.pid})"
        )
    # DELETE FIRST: a generic `open -a Google Chrome` can activate or launch
    # the user's normal Chrome profile as a second visible owner. The isolated
    # --app process above is the sole UI launch.
    # DELETED ASSUMPTION: argv remote-debugging flags are not DevTools ready.
    # Fail the headed launch and clean the ephemeral profile when the
    # loopback attach endpoint never answers with a loopback websocket.
    try:
        ready_facts = wait_chrome_devtools_ready()
    except PackagingError as exc:
        cleanup = close_disconnected_chrome_app_shell(
            helper_pid=helper.pid,
            profile_dir=str(profile_dir),
        )
        raise PackagingError(
            f"{exc}; chrome_devtools_cleanup="
            f"profile_deleted={cleanup.get('chrome_ephemeral_profile_deleted')}"
        ) from exc
    return {
        "ui_application": UI_APPLICATION_CHROME,
        "ui_application_path": str(chrome),
        "ui_launch_method": "chrome_bin --app=<editor_url>",
        "chrome_app_mode": True,
        "chrome_app_mode_helper_pid": helper.pid,
        "chrome_devtools_http_base": f"http://127.0.0.1:{CHROME_DEVTOOLS_PORT}",
        "chrome_devtools_loopback_only": True,
        "chrome_ephemeral_profile_dir": str(profile_dir),
        **ready_facts,
        **_unproven_headed_launch_facts(
            ui_launch_requested=True,
            # Request accepted means the helper spawned; activate exit is
            # recorded separately and is not treated as visibility.
            launchservices_request_accepted=True,
        ),
    }


def _owned_asciiid_chrome_app_shell_processes() -> list[dict[str, Any]]:
    """Return only prior isolated Chrome app processes owned by this product."""
    completed = subprocess.run(
        ["ps", "-axo", "pid=,command="],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise PackagingError(
            "cannot establish sole Chrome shell ownership: process census failed"
        )
    app_prefix = f"--app=http://127.0.0.1:{SHELL_PORT}/"
    profile_prefix = "--user-data-dir="
    owned: list[dict[str, Any]] = []
    for raw_line in completed.stdout.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        pid_text, separator, command = stripped.partition(" ")
        if not separator or not pid_text.isdigit():
            continue
        if str(CHROME_BIN) not in command or app_prefix not in command:
            continue
        profile_dir: str | None = None
        for argument in command.split():
            if argument.startswith(profile_prefix):
                candidate = argument[len(profile_prefix) :]
                if Path(candidate).name.startswith("asciiid-html-chrome-profile-"):
                    profile_dir = candidate
                break
        if profile_dir is None:
            continue
        owned.append({"pid": int(pid_text), "profile_dir": profile_dir})
    return owned


def retire_owned_asciiid_chrome_app_shells() -> dict[str, Any]:
    """Delete prior product-owned app shells before creating the sole new shell."""
    owned = _owned_asciiid_chrome_app_shell_processes()
    retired_pids: list[int] = []
    for process in owned:
        pid = int(process["pid"])
        close_disconnected_chrome_app_shell(
            helper_pid=pid,
            profile_dir=str(process["profile_dir"]),
        )
        retired_pids.append(pid)
    deadline = time.monotonic() + 5.0
    remaining = list(retired_pids)
    while remaining and time.monotonic() < deadline:
        still_alive: list[int] = []
        for pid in remaining:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            except (PermissionError, OSError):
                still_alive.append(pid)
            else:
                still_alive.append(pid)
        remaining = still_alive
        if remaining:
            time.sleep(0.05)
    if remaining:
        raise PackagingError(
            "cannot establish sole Chrome shell ownership: stale ASCIIID "
            f"processes remain alive after retirement: {remaining}"
        )
    return {
        "stale_chrome_app_shells_found": len(owned),
        "stale_chrome_app_shells_retired": len(retired_pids),
        "stale_chrome_app_shell_pids": retired_pids,
        "sole_chrome_shell_prelaunch_enforced": True,
    }


def close_disconnected_chrome_app_shell(
    *,
    helper_pid: int | None = None,
    profile_dir: str | None = None,
) -> dict[str, Any]:
    """
    Close the Chrome app-mode authoring shell when the backend dies.

    Why: a visible HTML window with a dead authoring API is a disconnected
    product shell presented as the editor. Packaging must not leave that
    as the lasting UI. This is lifecycle cleanup, not headed proof.
    """
    closed_via: list[str] = []
    # DELETE FIRST: title-wide AppleScript closure was a second lifecycle owner.
    # It could close another launch, a normal Chrome tab with the same page title,
    # and a shell whose process identity was never established. The isolated
    # --app helper process group recorded in the receipt is the sole close owner.
    helper_signal = None
    if helper_pid and helper_pid > 1:
        try:
            os.killpg(helper_pid, signal.SIGTERM)
            helper_signal = "SIGTERM_process_group"
            closed_via.append("helper_process_group")
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(helper_pid, signal.SIGTERM)
                helper_signal = "SIGTERM_pid"
                closed_via.append("helper_pid")
            except (ProcessLookupError, PermissionError, OSError):
                helper_signal = "helper_already_gone"
    profile_deleted = False
    if profile_dir:
        profile_path = Path(profile_dir)
        if profile_path.name.startswith("asciiid-html-chrome-profile-"):
            shutil.rmtree(profile_path, ignore_errors=True)
            profile_deleted = not profile_path.exists()
    return {
        "disconnected_chrome_shell_closed": helper_signal is not None,
        "chrome_shell_windows_closed_count": 0,
        "chrome_shell_close_via": closed_via,
        "chrome_app_mode_helper_pid": helper_pid,
        "chrome_app_mode_helper_signal": helper_signal,
        "chrome_ephemeral_profile_deleted": profile_deleted,
        # Explicit: this cleanup is never physical-action success.
        "physical_user_action_verified": False,
    }


def _read_prior_headed_receipt() -> dict[str, Any] | None:
    """
    Read ONLY this package tree's own prior HEADED runtime receipt.

    Returns the parsed dict, or None when there is no prior headed receipt (a
    fresh launch) or the file is unreadable/malformed. The non-headed smoke
    receipt is deliberately ignored — single-session replacement is a headed
    concern only.
    """
    # The prior session is always recorded in the HEADED receipt, regardless of
    # whether THIS launch is headed or a non-headed smoke — so target the headed
    # receipt explicitly rather than the (possibly smoke) active receipt path.
    receipt_path = PACKAGE_RECEIPT.parent / "runtime_receipt.json"
    if not receipt_path.is_file():
        return None
    try:
        parsed = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _ps_command_for_pid(pid: int) -> str | None:
    """
    Return the full command line of ``pid`` via ``ps``, or None if not running.

    Used to validate a receipt-recorded backend PID against the EXACT packaged
    binary command before terminating it — so a REUSED PID (now some unrelated
    program) is never killed.
    """
    completed = subprocess.run(
        ["ps", "-p", str(int(pid)), "-o", "command="],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    command = (completed.stdout or "").strip()
    return command or None


def _authoring_port_listener_pids(
    authoring_ports: tuple[int, ...],
) -> dict[int, int | None]:
    """
    Map each authoring port to the PID of its LISTEN holder (or None if free).

    Uses ``lsof`` per port. A port held by a PID other than our validated prior
    backend is a FOREIGN listener and fails the single-session gate shut.
    """
    holders: dict[int, int | None] = {}
    for port in authoring_ports:
        completed = subprocess.run(
            ["lsof", f"-iTCP:{int(port)}", "-sTCP:LISTEN", "-n", "-P"],
            capture_output=True,
            text=True,
            check=False,
        )
        holder: int | None = None
        for line in (completed.stdout or "").splitlines():
            if not line or line.startswith("COMMAND"):
                continue
            fields = line.split()
            # lsof columns: COMMAND PID USER ... — the PID is the 2nd field.
            if len(fields) >= 2 and fields[1].isdigit():
                holder = int(fields[1])
                break
        holders[int(port)] = holder
    return holders


def classify_prior_session(
    receipt: dict[str, Any] | None,
    *,
    receipt_present: bool,
    expected_launcher_command: str,
    expected_native_command: str,
    shell_port: int,
    authoring_port: int,
    pid_command: dict[int, str | None],
    port_listener_pids: dict[int, int | None],
) -> dict[str, Any]:
    """
    PURE single-session prelaunch decision (no process/port side effects).

    Real ownership tuple (FL-4588 PRELAUNCH-RECEIPT-LAUNCHER-CORRECTION): the
    LONG-LIVED launcher PID owns the shell server on ``shell_port`` (43125) and
    is the parent of the native backend, which owns ``authoring_port`` (43124).
    A prior session is retired by terminating the LAUNCHER (its finally block
    then releases both ports and cascades to its native + Chrome children).
    Ownership is NEVER inferred from ports alone — a live listener is validated
    against the recorded PID that must own it.

    Inputs are injected snapshots so this is fully unit-testable:
      * receipt — parsed prior headed receipt dict, or None (absent/unreadable).
      * receipt_present — whether the headed receipt FILE existed at all (lets us
        tell a DELETED-while-live session from a genuinely fresh launch).
      * expected_launcher_command / expected_native_command — the exact packaged
        launcher-script and native-binary command substrings each recorded PID
        MUST contain before it may be terminated.
      * shell_port / authoring_port — the two ports this single-session owns.
      * pid_command — {recorded_pid: live command line or None}.
      * port_listener_pids — {port: LISTEN-holder pid or None}.

    Returns {"action", "target_launcher_pid", "target_native_pid",
    "chrome_helper_pid", "blocker_kind", "reason", "held_ports"} where action is
    one of "proceed" | "retire" | "fail_shut". Every fail-shut carries a NAMED
    blocker_kind and never triggers a broad kill.
    """
    def result(action: str, **extra: Any) -> dict[str, Any]:
        base = {
            "action": action,
            "target_launcher_pid": None,
            "target_native_pid": None,
            "chrome_helper_pid": None,
            "blocker_kind": None,
            "reason": "",
            "held_ports": {},
        }
        base.update(extra)
        return base

    shell_holder = port_listener_pids.get(shell_port)
    authoring_holder = port_listener_pids.get(authoring_port)
    held = {
        port: pid
        for port, pid in (
            (shell_port, shell_holder),
            (authoring_port, authoring_holder),
        )
        if pid
    }

    # 1. Receipt FILE existed but could not be parsed as a dict -> malformed.
    if receipt_present and not isinstance(receipt, dict):
        return result(
            "fail_shut",
            blocker_kind="receipt_malformed",
            reason="prior headed receipt present but malformed/unreadable",
            held_ports=held,
        )

    # 2. No receipt at all. A live listener with NO receipt means the session
    #    receipt was deleted (e.g. a package rebuild) while the session is live.
    if not receipt_present:
        if held:
            return result(
                "fail_shut",
                blocker_kind="receipt_deleted_live_session",
                reason=(
                    "no prior headed receipt but live listener(s) hold owned "
                    f"port(s) {held} — session receipt deleted while live; "
                    "refuse broad kill"
                ),
                held_ports=held,
            )
        return result("proceed", reason="no prior receipt; owned ports free")

    # 3. Receipt dict present — extract the ownership tuple.
    chrome_helper_pid = receipt.get("chrome_app_mode_helper_pid")
    if not (isinstance(chrome_helper_pid, int) and chrome_helper_pid > 0):
        chrome_helper_pid = None
    launcher_pid = receipt.get("launcher_pid")
    native_pid = receipt.get("native_pid")
    launcher_pid = launcher_pid if isinstance(launcher_pid, int) and launcher_pid > 0 else None
    native_pid = native_pid if isinstance(native_pid, int) and native_pid > 0 else None
    if launcher_pid is None or native_pid is None:
        return result(
            "fail_shut",
            blocker_kind="receipt_missing_pids",
            reason=(
                "prior headed receipt missing launcher_pid/native_pid "
                "ownership tuple"
            ),
            held_ports=held,
            chrome_helper_pid=chrome_helper_pid,
        )

    launcher_cmd = pid_command.get(launcher_pid)
    native_cmd = pid_command.get(native_pid)
    launcher_running = launcher_cmd is not None
    native_running = native_cmd is not None

    # 4. Both recorded PIDs gone. Clean post-exit (ports free) -> proceed; a
    #    surviving listener on a stale session is anomalous -> fail shut.
    if not launcher_running and not native_running:
        if held:
            return result(
                "fail_shut",
                blocker_kind="stale_receipt_surviving_listener",
                reason=(
                    f"prior launcher {launcher_pid}/native {native_pid} not "
                    f"running (stale) but owned port(s) still held {held}"
                ),
                held_ports=held,
                chrome_helper_pid=chrome_helper_pid,
            )
        return result(
            "proceed",
            reason=(
                f"prior session (launcher {launcher_pid}/native {native_pid}) "
                "fully exited; owned ports free"
            ),
            chrome_helper_pid=chrome_helper_pid,
        )

    # 5. A running recorded PID must run its EXACT expected command (reuse guard).
    if launcher_running and expected_launcher_command not in launcher_cmd:
        return result(
            "fail_shut",
            blocker_kind="launcher_pid_reused",
            reason=(
                f"recorded launcher PID {launcher_pid} reused by a different "
                "command — refuse to terminate a non-owned process"
            ),
            held_ports=held,
        )
    if native_running and expected_native_command not in native_cmd:
        return result(
            "fail_shut",
            blocker_kind="native_pid_reused",
            reason=(
                f"recorded native PID {native_pid} reused by a different "
                "command — refuse to terminate a non-owned process"
            ),
            held_ports=held,
        )

    # 6. Partial survival — exactly one of the ownership tuple is alive.
    if launcher_running != native_running:
        return result(
            "fail_shut",
            blocker_kind="partial_session_survival",
            reason=(
                "prior session partially alive "
                f"(launcher_running={launcher_running}, "
                f"native_running={native_running}) — refuse targeted kill of a "
                "half-dead session"
            ),
            held_ports=held,
            chrome_helper_pid=chrome_helper_pid,
        )

    # 7. Both running + exact match. Validate PORT ownership explicitly — the
    #    shell port must be held by the launcher, the authoring port by the
    #    native (or be free). Any other holder is a foreign listener.
    foreign: dict[int, int] = {}
    if shell_holder is not None and shell_holder != launcher_pid:
        foreign[shell_port] = shell_holder
    if authoring_holder is not None and authoring_holder != native_pid:
        foreign[authoring_port] = authoring_holder
    if foreign:
        return result(
            "fail_shut",
            blocker_kind="foreign_listener",
            reason=(
                f"owned port(s) held by foreign listener {foreign} alongside a "
                f"valid prior session (launcher {launcher_pid}/native "
                f"{native_pid}) — refuse broad kill"
            ),
            held_ports=held,
        )

    # 8. Valid live prior session -> retire by terminating the LAUNCHER (its
    #    finally block releases both ports and cascades to its children).
    return result(
        "retire",
        target_launcher_pid=launcher_pid,
        target_native_pid=native_pid,
        chrome_helper_pid=chrome_helper_pid,
        reason=(
            f"valid prior single-session launcher {launcher_pid} "
            f"(native {native_pid}) to retire"
        ),
        held_ports=held,
    )


def _terminate_validated_prior_launcher(launcher_pid: int) -> None:
    """
    SIGTERM ONLY the validated prior LAUNCHER PID (never a process group, never a
    broad sweep). The caller has already confirmed this PID runs the exact
    packaged launcher script, so a targeted terminate cannot hit an unrelated
    process. The launcher's own finally block then shuts down the shell server
    (releasing SHELL_PORT) and SIGTERMs the native backend (releasing
    AUTHORING_PORT) — a single targeted signal cascades to the whole session.
    """
    try:
        os.kill(int(launcher_pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        # Already gone or unsignalable — the wait/verify step decides fail-shut.
        pass


def _pid_alive(pid: int) -> bool:
    """True if ``pid`` is still a live process (signal-0 probe)."""
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        # Exists but not ours to signal — still counts as alive/surviving.
        return True


def _wait_prior_session_gone(
    launcher_pid: int,
    native_pid: int,
    shell_port: int,
    authoring_port: int,
    *,
    deadline_s: float = 10.0,
) -> None:
    """
    Wait for BOTH the launcher and native PIDs to EXIT and for BOTH owned ports
    (shell + authoring) to RELEASE. Fails shut (PackagingError, NO broad kill)
    with a named blocker if any PID or port survives once the deadline elapses.
    """
    ports = (shell_port, authoring_port)
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        alive = _pid_alive(launcher_pid) or _pid_alive(native_pid)
        held = {
            p: pid
            for p, pid in _authoring_port_listener_pids(ports).items()
            if pid
        }
        if not alive and not held:
            return
        time.sleep(0.1)
    surviving_pids = [p for p in (launcher_pid, native_pid) if _pid_alive(p)]
    if surviving_pids:
        raise PackagingError(
            "pre-native single-session lifecycle [surviving_pid]: prior PID(s) "
            f"{surviving_pids} survived launcher termination (no broad kill) — "
            "fail shut"
        )
    held = {
        p: pid
        for p, pid in _authoring_port_listener_pids(ports).items()
        if pid
    }
    if held:
        raise PackagingError(
            "pre-native single-session lifecycle [surviving_port]: owned port(s) "
            f"still held after prior session exit: {held} — fail shut"
        )


def retire_prior_single_session_before_native() -> dict[str, Any]:
    """
    Sole PRE-NATIVE single-session lifecycle owner.

    Runs once, BEFORE the native ``subprocess.Popen``. It:
      1. reads ONLY this package tree's own prior headed receipt,
      2. validates any recorded backend PID against the EXACT packaged binary
         command + authoring port,
      3. terminates ONLY that validated prior backend,
      4. retires exact ASCIIID-owned Chrome app process groups (reusing the
         preserved exact-match + process-group close + wait-for-exit owner),
      5. waits for the backend PID to exit AND ports 43124/43125 to release.

    Then the caller starts the new native. A stale, missing, malformed,
    PID-reused, foreign-listener, surviving-PID, surviving-port, or
    receipt-deleted-live session fails shut with a NAMED blocker WITHOUT any
    broad kill, and never infers ownership from ports alone.
    """
    # The prior launcher ran THIS same packaged script; the prior native ran the
    # packaged binary on the authoring port. Both are exact-command validated.
    expected_launcher_command = str(Path(__file__).resolve())
    expected_native_command = f"{PACKAGE_BIN} --authoring-port {AUTHORING_PORT}"
    receipt_present = (PACKAGE_RECEIPT.parent / "runtime_receipt.json").is_file()
    receipt = _read_prior_headed_receipt()
    launcher_pid: int | None = None
    native_pid: int | None = None
    if isinstance(receipt, dict):
        if isinstance(receipt.get("launcher_pid"), int):
            launcher_pid = int(receipt["launcher_pid"])
        if isinstance(receipt.get("native_pid"), int):
            native_pid = int(receipt["native_pid"])
    # Snapshot the exact facts the pure classifier needs (both recorded PIDs).
    pid_command: dict[int, str | None] = {}
    if launcher_pid and launcher_pid > 0:
        pid_command[launcher_pid] = _ps_command_for_pid(launcher_pid)
    if native_pid and native_pid > 0:
        pid_command[native_pid] = _ps_command_for_pid(native_pid)
    port_listener_pids = _authoring_port_listener_pids((AUTHORING_PORT, SHELL_PORT))
    decision = classify_prior_session(
        receipt,
        receipt_present=receipt_present,
        expected_launcher_command=expected_launcher_command,
        expected_native_command=expected_native_command,
        shell_port=SHELL_PORT,
        authoring_port=AUTHORING_PORT,
        pid_command=pid_command,
        port_listener_pids=port_listener_pids,
    )
    action = decision["action"]
    if action == "fail_shut":
        raise PackagingError(
            "pre-native single-session lifecycle refused (no broad kill) "
            f"[{decision['blocker_kind']}]: {decision['reason']}"
        )
    facts: dict[str, Any] = {
        "prelaunch_single_session_action": action,
        "prelaunch_single_session_reason": decision["reason"],
        "prelaunch_prior_launcher_pid": decision.get("target_launcher_pid"),
        "prelaunch_prior_native_pid": decision.get("target_native_pid"),
    }
    # Retire owned Chrome app shells exactly once here (pre-native), reusing the
    # preserved exact-match + process-group close owner.
    facts.update(retire_owned_asciiid_chrome_app_shells())
    if action == "retire":
        target_launcher = int(decision["target_launcher_pid"])
        target_native = int(decision["target_native_pid"])
        # Terminate ONLY the validated launcher; its finally block releases both
        # ports and cascades to the native backend + Chrome children.
        _terminate_validated_prior_launcher(target_launcher)
        _wait_prior_session_gone(
            target_launcher, target_native, SHELL_PORT, AUTHORING_PORT
        )
        facts["prelaunch_prior_session_retired"] = True
    return facts


def launch_package(
    *,
    open_browser: bool = True,
    health_timeout_s: float = 45.0,
    from_app_resources: bool = False,
    app_resources: Path | None = None,
    authoring_port: int = AUTHORING_PORT,
    shell_port: int = SHELL_PORT,
    isolated_state_dir: Path | None = None,
) -> dict[str, Any]:
    """
    Start the packaged native runtime + shell server and open Chrome.

    Fail-closed: missing package bits, missing token, unreachable health,
    or inability to launch Google Chrome write a FAIL receipt and raise.

    HTML-only launch contract: Google Chrome is the sole visible application.
    Any visible native product window fails the launch closed.
    The sole launch path is explicit app Resources. Flat package launch was
    deleted because it could not prove source-to-bundle identity.
    """
    global PACKAGE_RECEIPT, PACKAGE_RECEIPT_NON_HEADED_SMOKE
    global RUNTIME_STATE_DIR_OVERRIDE

    if not (1 <= authoring_port <= 65535 and 1 <= shell_port <= 65535):
        raise PackagingError("launch ports must be in 1..65535")
    if authoring_port == shell_port:
        raise PackagingError("authoring and shell ports must be distinct")
    isolated_test_ports = (
        authoring_port != AUTHORING_PORT or shell_port != SHELL_PORT
    )
    if isolated_test_ports:
        if open_browser:
            raise PackagingError("isolated test ports require --no-browser")
        if authoring_port == AUTHORING_PORT or shell_port == SHELL_PORT:
            raise PackagingError(
                "isolated test launch must replace both production ports"
            )
        if isolated_state_dir is None:
            raise PackagingError(
                "isolated test launch requires a separate runtime-state directory"
            )
    elif isolated_state_dir is not None:
        raise PackagingError(
            "separate runtime-state directory is valid only with isolated test ports"
        )
    if from_app_resources is not True or app_resources is None:
        raise PackagingError(
            "flat package launch owner deleted; launch requires sealed app Resources"
        )

    app_launch_facts: dict[str, Any] = {}
    native_cwd = ROOT
    # An ambient app-resource variable cannot silently rebind the package.
    # Leftover env from a prior .app launch must not silently retarget
    # Deleted flat launch paths must never inherit an ambient Resources target.
    # Only explicit from_app_resources / app_resources= may rebind.
    if from_app_resources:
        resources = Path(app_resources).resolve()
        if not resources.exists():
            raise PackagingError(
                f"app Resources path does not exist: {resources}"
            )
        configure_paths_from_app_resources(resources)
        freshness = verify_freshness_receipt(resources)
        native_cwd = resources
        # Prefer the enclosing .app path when Resources sits under Contents/.
        bundle_guess = resources.parent.parent
        app_launch_facts = {
            "app_bundle": _relpath(bundle_guess)
            if bundle_guess.name.endswith(".app")
            else _relpath(resources),
            "app_resources": str(resources),
            "app_native_sha256": _sha256_file(PACKAGE_BIN),
            "app_shell_index_sha256": _sha256_file(PACKAGE_SHELL / "index.html"),
            "app_registry_sha256": _registry_contract_hash(
                PACKAGE_SHELL / "asciiid.ui_registry.v1.json"
            ),
            "launch_path_owner": "app_bundle_resources",
            "repo_relative_package_paths": False,
            "freshness_receipt_sha256": freshness["freshness_receipt_sha256"],
            "package_identity_sha256": freshness["package_identity_sha256"],
            "package_freshness_verified": True,
        }

    if isolated_test_ports:
        test_state = Path(isolated_state_dir).resolve()
        test_state.mkdir(parents=True, exist_ok=True)
        PACKAGE_RECEIPT = test_state / "runtime_receipt.json"
        PACKAGE_RECEIPT_NON_HEADED_SMOKE = (
            test_state / "runtime_receipt.non_headed_smoke.json"
        )
        RUNTIME_STATE_DIR_OVERRIDE = test_state
        app_launch_facts["isolated_test_state_dir"] = str(test_state)
    else:
        RUNTIME_STATE_DIR_OVERRIDE = None

    # DELETE FIRST (stale-launch recovery): remove any handoff left by a prior
    # or crashed launch before this launch writes its own. PACKAGE_ROOT is now
    # bound to the active runtime directory (default checkout or .app Resources),
    # so this clears the handoff belonging to the tree we are about to launch.
    delete_evidence_viewer_handoff()

    if not PACKAGE_BIN.is_file() or not (PACKAGE_SHELL / "index.html").is_file():
        reason = (
            "sealed app package incomplete "
            f"(need {PACKAGE_BIN} and {PACKAGE_SHELL / 'index.html'})"
        )
        fail_receipt(reason, **app_launch_facts)
        raise PackagingError(reason)

    assert_registry_editor_cutover_complete(
        PACKAGE_SHELL / "asciiid.ui_registry.v1.json"
    )

    # Headed product receipt vs non-headed smoke receipt — never mixed.
    # Packaging tests used --no-browser against the real .app Resources and
    # overwrote runtime_receipt.json with ui_application_skipped, so operators
    # saw a native window and thought headed HTML launch "worked".
    if open_browser:
        PACKAGE_RECEIPT = (
            PACKAGE_RECEIPT
            if PACKAGE_RECEIPT.name == "runtime_receipt.json"
            else PACKAGE_RECEIPT.parent / "runtime_receipt.json"
        )
    else:
        PACKAGE_RECEIPT = PACKAGE_RECEIPT_NON_HEADED_SMOKE
        print(
            "[asciiid-html-desktop] NON-HEADED SMOKE — writing "
            f"{PACKAGE_RECEIPT.name} (not the headed product receipt)",
            flush=True,
        )

    # FL-4588 PRELAUNCH-SINGLE-SESSION: production keeps the SOLE pre-native
    # lifecycle owner on fixed ports. Non-headed integration tests use two
    # explicit isolated ports and may never inspect, retire, or otherwise touch
    # a live product session. They only prove their own ports are free.
    if isolated_test_ports:
        held_test_ports = {
            port: pid
            for port, pid in _authoring_port_listener_pids(
                (authoring_port, shell_port)
            ).items()
            if pid is not None
        }
        if held_test_ports:
            raise PackagingError(
                f"isolated test port already held: {held_test_ports}"
            )
        app_launch_facts["isolated_test_ports"] = True
    else:
        app_launch_facts.update(retire_prior_single_session_before_native())

    env = os.environ.copy()
    # Packaging lane: force the hidden-backend contract even if a stale
    # binary missed the --authoring-port flag wiring.
    env["ASCIIID_HTML_SHELL_BACKEND"] = "1"
    native = subprocess.Popen(
        [str(PACKAGE_BIN), "--authoring-port", str(authoring_port)],
        cwd=str(native_cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    shell_server = None
    stdout_drain: NativeStdoutTokenDrain | None = None
    try:
        token, startup_log, stdout_drain = _read_token_from_stream(native.stdout)
        health = _wait_health(
            timeout_s=health_timeout_s,
            health_url=f"http://127.0.0.1:{authoring_port}/api/v1/health",
        )
        bundled_registry_hash = _registry_contract_hash(
            PACKAGE_SHELL / "asciiid.ui_registry.v1.json"
        )
        if health.get("ui_registry_sha256") != bundled_registry_hash:
            raise PackagingError(
                "native health registry hash does not match the bundled shell registry"
            )
        shell_server = _start_shell_server(PACKAGE_SHELL, shell_port, token)
        editor_url = f"http://127.0.0.1:{shell_port}/#/scene/terrain/sculpt"
        # FL-4588 evidence-viewer handoff: the native token, backend pid, git
        # head, registry hash, and both origins are now known — write the ONE
        # ephemeral 0600 read-only credential handoff. Delete-first, atomic
        # replacement, and fail-closed permission checks live inside the writer.
        # The plaintext token is never stored (only its derived hash), so this
        # never leaks the token to disk, receipts, or logs.
        write_evidence_viewer_handoff(
            session_token=token,
            backend_pid=native.pid,
            git_head=_git_head(),
            ui_registry_sha256=bundled_registry_hash,
            native_origin=f"http://127.0.0.1:{authoring_port}",
            shell_origin=f"http://127.0.0.1:{shell_port}",
        )
        chrome_facts: dict[str, Any]
        if open_browser:
            # Headed path: Chrome is the only requested product UI.
            # Spawning Chrome is a request only — never visibility proof.
            chrome_facts = _open_editor_in_chrome(editor_url)
            visibility = observe_chrome_shell_visibility()
            chrome_facts.update(visibility)
            shell_ready = observe_chrome_authoring_ready_cdp(shell_port=shell_port)
            chrome_facts.update(shell_ready)
            native_vis = observe_zero_visible_native_product_windows(native.pid)
            chrome_facts.update(native_vis)
            chrome_facts.update(
                {
                    "physical_user_action_verified": False,
                    "physical_action_marker": None,
                    "physical_action_method": "not_owned_by_html_only_launch",
                    "physical_action_blocker": (
                        "HTML-only launch observes Chrome + zero native windows; "
                        "widget mutation proof is out of scope"
                    ),
                }
            )
            html_only_ok = (
                visibility.get("browser_visibility_verified") is True
                and shell_ready.get("html_editor_loaded_verified") is True
                and shell_ready.get("shell_ready_via_cdp") is True
                and shell_ready.get("shell_live_authoring_session_verified") is True
                and shell_ready.get("shell_live_snapshot_verified") is True
                and shell_ready.get("shell_real_navigation_verified") is True
                and native_vis.get("native_product_windows_absent_verified") is True
            )
            chrome_facts["html_only_visibility_verified"] = html_only_ok
            # Diagnostic only — never physical-action / product-success claim.
            chrome_facts["physical_user_action_verified"] = False
            if html_only_ok:
                chrome_facts["proof_status"] = DIAGNOSTIC_STATUS_HTML_ONLY_VISIBILITY
            else:
                chrome_facts["proof_status"] = PROOF_STATUS_HEADED_LAUNCH_PENDING
            print(
                "[asciiid-html-desktop] HTML product readiness "
                f"chrome={chrome_facts.get('browser_visibility_verified')} "
                f"session={chrome_facts.get('shell_live_authoring_session_verified')} "
                f"snapshot={chrome_facts.get('shell_live_snapshot_verified')} "
                f"navigation={chrome_facts.get('shell_real_navigation_verified')} "
                f"native_absent="
                f"{chrome_facts.get('native_product_windows_absent_verified')} "
                f"chrome_blocker={chrome_facts.get('chrome_visibility_blocker')!r} "
                f"shell_blocker={chrome_facts.get('shell_ready_blocker')!r} "
                f"native_blocker="
                f"{chrome_facts.get('native_product_windows_blocker')!r}",
                flush=True,
            )
            if native_vis.get("native_product_windows_absent_verified") is not True:
                raise PackagingError(
                    native_vis.get("native_product_windows_blocker")
                    or "visible native product window forbidden"
                )
            if visibility.get("browser_visibility_verified") is not True:
                raise PackagingError(
                    visibility.get("chrome_visibility_blocker")
                    or "Chrome HTML shell window not visibly observed"
                )
            if shell_ready.get("html_editor_loaded_verified") is not True:
                raise PackagingError(
                    shell_ready.get("shell_ready_blocker")
                    or "Chrome HTML authoring shell readiness not observed"
                )
        else:
            # Non-headed smoke: no Chrome; cannot claim HTML-only headed proof.
            chrome_facts = {
                "ui_application": None,
                "ui_application_skipped": True,
                "visible_native_product_windows_required": 0,
                "html_only_visibility_verified": False,
                **_unproven_headed_launch_facts(
                    ui_launch_requested=False,
                    launchservices_request_accepted=False,
                ),
            }

        # Prefer the live bounded tail after health — drain may have advanced.
        live_tail = (
            stdout_drain.diagnostic_tail() if stdout_drain is not None else startup_log
        )
        receipt = {
            "schema": "asciiid.html_desktop_runtime_receipt.v1",
            "status": "LAUNCHED",
            "created_at_utc": _utc_now(),
            "package_root": _relpath(PACKAGE_ROOT),
            "authoring_port": authoring_port,
            "shell_port": shell_port,
            "fail_closed": True,
            "git_head": _git_head(),
            "phase": "launch_from_app_resources"
            if app_launch_facts
            else "launch",
            "native_pid": native.pid,
            # FL-4588 PRELAUNCH-SINGLE-SESSION: the LONG-LIVED launcher process
            # (this PID) owns the shell server on SHELL_PORT and is the parent of
            # both the native backend (AUTHORING_PORT) and the Chrome app shell.
            # Retiring a prior session means terminating this launcher PID so its
            # finally block releases both ports and cascades to its children.
            "launcher_pid": os.getpid(),
            "native_bin": _relpath(PACKAGE_BIN),
            "native_sha256": _sha256_file(PACKAGE_BIN),
            "health_ok": bool(health.get("ok")),
            "health_capability": health.get("capability"),
            "health_product_ready": bool(health.get("product_ready")),
            "health_snapshot_ready": bool(health.get("snapshot_ready")),
            "health_termpp_final_ready": bool(health.get("termpp_final_ready")),
            "mutation_enabled": health.get("mutation_enabled"),
            "ui_registry_sha256": health.get("ui_registry_sha256"),
            "editor_url_host": f"http://127.0.0.1:{shell_port}/",
            "token_observed": True,
            "startup_log_tail": live_tail[-STDOUT_DIAGNOSTIC_TAIL_CHARS:],
            "receipt_stage": "post_cdp_authoring_readiness",
            **chrome_facts,
            **app_launch_facts,
        }
        write_receipt(receipt)
        print(
            f"[asciiid-html-desktop] LAUNCHED pid={native.pid} "
            f"api=http://127.0.0.1:{authoring_port} shell={editor_url} "
            f"ui={receipt.get('ui_application') or 'skipped'}",
            flush=True,
        )
        print(
            f"[asciiid-html-desktop] receipt={_relpath(PACKAGE_RECEIPT)}",
            flush=True,
        )

        chrome_helper_pid = chrome_facts.get("chrome_app_mode_helper_pid")
        lifecycle_facts: dict[str, Any] = {}
        try:
            lifecycle_facts = wait_for_product_lifecycle(
                native,
                open_browser=open_browser,
                chrome_helper_pid=(
                    int(chrome_helper_pid)
                    if isinstance(chrome_helper_pid, int)
                    else None
                ),
                shell_port=shell_port,
            )
        except KeyboardInterrupt:
            print("[asciiid-html-desktop] interrupt — stopping", flush=True)
            lifecycle_facts = {"lifecycle_exit_reason": "launcher_interrupted"}
        if (
            stdout_drain is not None
            and lifecycle_facts.get("lifecycle_exit_reason")
            == "native_backend_exited"
        ):
            stdout_drain.join(timeout=2.0)
        final_stdout_tail = (
            stdout_drain.diagnostic_tail()
            if stdout_drain is not None
            else receipt.get("startup_log_tail", "")
        )
        receipt = {
            **receipt,
            **lifecycle_facts,
            "startup_log_tail": final_stdout_tail[
                -STDOUT_DIAGNOSTIC_TAIL_CHARS:
            ],
            "native_stdout_bytes_consumed_after_token": (
                stdout_drain.bytes_consumed_after_token
                if stdout_drain is not None
                else 0
            ),
        }
        write_receipt(receipt)
        # Backend death / launcher exit must not leave a disconnected Chrome
        # shell presented as the editor. Lifecycle cleanup only — not proof.
        if open_browser:
            close_facts = close_disconnected_chrome_app_shell(
                helper_pid=int(chrome_helper_pid)
                if isinstance(chrome_helper_pid, int)
                else None,
                profile_dir=chrome_facts.get("chrome_ephemeral_profile_dir")
                if isinstance(chrome_facts.get("chrome_ephemeral_profile_dir"), str)
                else None,
            )
            receipt = {**receipt, **close_facts}
            write_receipt(receipt)
            print(
                "[asciiid-html-desktop] closed disconnected Chrome app shell "
                f"via={close_facts.get('chrome_shell_close_via')!r} "
                f"windows={close_facts.get('chrome_shell_windows_closed_count')}",
                flush=True,
            )
        return receipt
    except Exception as exc:
        # Also close any Chrome app shell opened before the failure.
        try:
            if open_browser and "chrome_facts" in locals():
                close_disconnected_chrome_app_shell(
                    helper_pid=chrome_facts.get("chrome_app_mode_helper_pid")
                    if isinstance(chrome_facts.get("chrome_app_mode_helper_pid"), int)
                    else None,
                    profile_dir=chrome_facts.get("chrome_ephemeral_profile_dir")
                    if isinstance(chrome_facts.get("chrome_ephemeral_profile_dir"), str)
                    else None,
                )
        except Exception:
            pass
        failure_facts = (
            chrome_facts if "chrome_facts" in locals() and isinstance(chrome_facts, dict)
            else {}
        )
        fail_receipt(
            str(exc),
            launcher_pid=os.getpid(),
            native_pid=getattr(native, "pid", None),
            **failure_facts,
            **app_launch_facts,
        )
        raise
    finally:
        # Every lifecycle exit — normal return, exception, or interrupt — removes
        # the ephemeral evidence-viewer credential so it never outlives the
        # session it belongs to. Best-effort delete: cleanup must not crash.
        delete_evidence_viewer_handoff()
        if shell_server is not None:
            shell_server.shutdown()
            shell_server.server_close()
        if native.poll() is None:
            native.send_signal(signal.SIGTERM)
            try:
                native.wait(timeout=10)
            except subprocess.TimeoutExpired:
                native.kill()
        if stdout_drain is not None:
            # Give the drain thread a moment to observe EOF after process exit.
            stdout_drain.join(timeout=2.0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Tracked macOS one-command package + launcher for the "
            "ASCIID FL4512 Presenter HTML Authoring client (FL-4588)."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the package plan JSON without building or launching.",
    )
    parser.add_argument(
        "--package-only",
        action="store_true",
        help=(
            "Fresh-build and seal dist/ASCIID FL4512 Presenter HTML Authoring.app with its mandatory "
            "freshness receipt; do not launch."
        ),
    )
    parser.add_argument(
        "--launch-from-app-resources",
        action="store_true",
        help=(
            "Launch using Contents/Resources paths only. Requires "
            f"{APP_RESOURCES_ENV} (set by the .app MacOS executable)."
        ),
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help=(
            "Non-headed smoke only. Writes runtime_receipt.non_headed_smoke.json "
            "so it cannot overwrite the headed product receipt. Chrome remains "
            "the headed product UI owner."
        ),
    )
    parser.add_argument(
        "--allow-non-headed-smoke",
        action="store_true",
        help=(
            "Required with --no-browser for packaging smoke tests. "
            "The product .app MacOS entry never passes this flag."
        ),
    )
    parser.add_argument(
        "--test-authoring-port",
        type=int,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--test-shell-port",
        type=int,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--test-state-dir",
        type=Path,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)

    try:
        if args.dry_run:
            plan = package(dry_run=True)
            print(json.dumps(plan, indent=2, sort_keys=True))
            return 0
        exclusive = [
            args.package_only,
            args.launch_from_app_resources,
        ]
        if sum(1 for flag in exclusive if flag) > 1:
            raise PackagingError(
                "choose one of --package-only / --launch-from-app-resources"
            )
        test_ports_requested = (
            args.test_authoring_port is not None
            or args.test_shell_port is not None
        )
        if test_ports_requested:
            if args.test_authoring_port is None or args.test_shell_port is None:
                raise PackagingError(
                    "isolated test launch requires both test ports"
                )
            if not (
                args.launch_from_app_resources and args.no_browser
            ):
                raise PackagingError(
                    "isolated test ports require a launch mode plus --no-browser"
                )
            if args.test_state_dir is None:
                raise PackagingError(
                    "isolated test ports require --test-state-dir"
                )
        elif args.test_state_dir is not None:
            raise PackagingError(
                "--test-state-dir requires isolated test ports"
            )
        if args.launch_from_app_resources:
            resources_env = os.environ.get(APP_RESOURCES_ENV)
            if not resources_env:
                raise PackagingError(
                    f"--launch-from-app-resources requires {APP_RESOURCES_ENV}"
                )
            if args.no_browser and not args.allow_non_headed_smoke:
                raise PackagingError(
                    "--no-browser with --launch-from-app-resources requires "
                    "--allow-non-headed-smoke (packaging smoke only). "
                    "The ASCIID FL4512 Presenter HTML Authoring.app entry always opens Chrome."
                )
            if args.allow_non_headed_smoke and not args.no_browser:
                raise PackagingError(
                    "--allow-non-headed-smoke is only valid with --no-browser"
                )
            launch_package(
                open_browser=not args.no_browser,
                from_app_resources=True,
                app_resources=Path(resources_env),
                authoring_port=(
                    args.test_authoring_port
                    if args.test_authoring_port is not None
                    else AUTHORING_PORT
                ),
                shell_port=(
                    args.test_shell_port
                    if args.test_shell_port is not None
                    else SHELL_PORT
                ),
                isolated_state_dir=args.test_state_dir,
            )
            return 0
        receipt = package(dry_run=False)
        print(json.dumps(receipt, indent=2, sort_keys=True))
        if args.package_only:
            return 0
        # Default one-launch path: run from the app Resources tree.
        resources = app_bundle_layout()["resources"]
        launch_package(
            open_browser=not args.no_browser,
            from_app_resources=True,
            app_resources=resources,
        )
        return 0
    except PackagingError as exc:
        print(f"[asciiid-html-desktop] FAIL: {exc}", file=sys.stderr)
        # DELETE FIRST (FL-4652): launch_package is the sole runtime-receipt
        # writer. The CLI boundary must not replace its PID-bound failure facts,
        # nor create an ownerless FAIL receipt when pre-native lifecycle checks
        # reject the launch before a backend exists.
        return 2


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Asciicker unified launcher.

Canonical launcher ownership lives in `scripts/launcher.py`.

Usage:
    python3 scripts/launcher.py
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import inspect
import json
import math
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).parent.parent.resolve()
_SCRIPTS_DIR = Path(__file__).parent.resolve()

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


from launcher_io import ConsoleManager as _ConsoleManager

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from scripts.launcher_lib import blender_paths as _blender_paths
from scripts.launcher_lib import health as _health
from scripts.launcher_lib import local_config as _lcfg
from scripts.launcher_lib import option_tree as _option_tree
from scripts.launcher_lib import pipeline_server as _pipeline
from scripts.launcher_lib import server_env as _senv
from scripts.launcher_lib import ui_fancy as _ui_fancy
from scripts.launcher_lib import wizard as _wizard
from scripts.pipeline import xp_assets_browser_layer_2_only as _xp_assets_layer2_only
from scripts.launcher_lib.banner import (
    _build_banner_str,
    _banner_target_cols,
)
from scripts import asciiid_app as _asciiid_app
from scripts.pipeline import xp_raw_layer_inspector as _xp_raw_layer_inspector
from scripts.slot_config_repository import (
    load_slot_config as _load_slot_config,
    SlotConfig as _SlotConfig,
    require_slot_targets as _require_slot_targets_shared,
)
from scripts.run_summary_query import (
    run_summary_path as _run_summary_path_shared,
    load_summary_file as _load_summary_file_shared,
    iter_run_summary_records as _iter_run_summary_records_shared,
    latest_run_id as _latest_run_id_shared,
    run_record_label as _run_record_label_shared,
    read_run_summary as _read_run_summary_shared,
    path_from_summary as _path_from_summary_shared,
    watchdog_artifact_status as _watchdog_artifact_status_shared,
)
from scripts.failure_ux import (
    show_failure_ux as _show_failure_ux,
    OperatorDecision as _OperatorDecision,
)
from scripts.tmp_clone_recovery import (
    TmpCloneRecoveryResult as _TmpCloneRecoveryResult,
    run_tmp_clone_recovery as _run_tmp_clone_recovery,
)
from scripts.bundle_hash_status import (
    collect_status as _bundle_hash_collect_status,
)

from launcher_rain import RainEngine as _RainEngine

try:
    from cli_style import Spinner, SPINNER_BLOCK, COLOR_PURPLE
except ImportError:
    Spinner = None


import contextlib as _contextlib

from scripts.launcher_ui import Renderer as _Renderer, RichFormatter as _RichFormatter, ScrollView as _ScrollView
from scripts.launcher_fuzzy_selector import fuzzy_select as _fuzzy_select

# Unified renderer (FL-1924: replaces dual rain-UI / Rich-console architecture)
_renderer = _Renderer()
_rich_fmt = _RichFormatter()

# ── ConsoleManager (FL-2790) ────────────────────────────────────────────────
# Owns the console-swap state machine, banner caches, and I/O helpers.
# The module-level ``console`` is kept for backward compatibility — 889+
# call sites write to it.  The ConsoleManager swaps it during buffer/capture
# operations and sets it back on stop/restore.
_AUDIT_MODE = False

_io_mgr = _ConsoleManager(_renderer, audit_mode=_AUDIT_MODE)
console = _io_mgr.console


def _sync_audit_mode() -> None:
    _io_mgr.set_audit_mode(_AUDIT_MODE)
    input_mgr = globals().get("_input_mgr")
    if input_mgr is not None:
        input_mgr.set_audit_mode(_AUDIT_MODE)


def _diagnostic_stream_is_stderr() -> bool:
    _sync_audit_mode()
    return _io_mgr._diagnostic_stream_is_stderr()


def _write_stdout_text(text: str) -> None:
    _io_mgr.write_stdout_text(text)


def _write_stdout_lines(lines: list[str]) -> None:
    _io_mgr.write_stdout_lines(lines)


@_contextlib.contextmanager
def _loading(msg: str = "Loading"):
    with _io_mgr.loading(msg):
        yield


def _start_submenu_buffer() -> None:
    global console
    _io_mgr.start_submenu_buffer()
    console = _io_mgr.console


def _read_buffer_lines() -> list[str]:
    return _io_mgr.read_buffer_lines()


def _flush_and_render(status: str = "") -> list[str]:
    return _io_mgr.flush_and_render(status)


def _stop_submenu_buffer() -> None:
    global console
    _io_mgr.stop_submenu_buffer()
    console = _io_mgr.console


@_contextlib.contextmanager
def _capturing_console():
    global console
    gen = _io_mgr.capturing_console()
    cap_io = gen.__enter__()
    try:
        console = _io_mgr.console
        yield cap_io
    finally:
        gen.__exit__(None, None, None)
        console = _io_mgr.console


def _cached_banner_lines(cols: int) -> list[str]:
    return _io_mgr.cached_banner_lines(cols)


def _cached_submenu_banner_lines(cols: int) -> list[str]:
    return _io_mgr.cached_submenu_banner_lines(cols)


# ── Audit mode ────────────────────────────────────────────────────────────────
# Ctrl+F (^F, \x06) fires from any _prompt_char menu to log an FL issue entry.
# Path is derived from the live Python call stack — no manual tracking needed.
# New menu functions are automatically covered as long as they follow the
# _menu_* or _edit_* naming convention, or are added to _MENU_FUNC_TITLES.
_AUDIT_FLAG_CHAR = "\x06"  # Ctrl+F in raw terminal mode

_AUDIT_CATEGORIES: list[tuple[str, str]] = [
    ("1", "Broken / does not execute"),
    ("2", "Misleading label / nonsensical"),
    ("3", "Bad UX flow / inconvenient"),
    ("4", "Silent failure / no feedback"),
    ("5", "Executes but produces wrong result"),
    ("6", "Missing feature / gap"),
    ("7", "Confusing copy or unclear intent"),
    ("8", "Other"),
]
_AUDIT_CATEGORIES_MAP: dict[str, str] = dict(_AUDIT_CATEGORIES)

# Maps internal function names to display titles for the audit breadcrumb.
# Functions matching _menu_* or _edit_* that are NOT listed here get an
# auto-derived title (underscores to spaces, title-case) so new menus
# appear automatically without requiring a manual registration step.
_MENU_FUNC_TITLES: dict[str, str] = {
    "_menu_game": "Game",
    "_menu_multiplayer": "Multiplayer",
    "_menu_multiplayer_join": "Join",
    "_menu_multiplayer_host": "Host",
    "_menu_host_local": "Host Local",
    "_menu_vps_operations_center": "VPS Operations Center",
    "_menu_vps_header": "Server Status",
    "_menu_analyze_runs": "Analyze Runs",
    "_menu_failure_log": "Failure Log",
    "_menu_deploy": "Deploy",
    "_menu_run_watchdog": "Run Watchdog",
    "_menu_trust_audit": "Legacy Health Check",
    "_menu_slot_management": "Slot Management",
    "_menu_mobile_playwright": "Mobile / Playwright",
    "_menu_xp_anchor_review": "Anchor Review",
    "_menu_recipes": "Recipes",
    "_menu_asset_map_editor": "Asset & Map Editor",
    "_menu_list_maps": "List Maps",
    "_menu_xp_asset_browser": "Sprite Asset Browser",
    "_menu_dev_tool_scripts": "Dev Tool Scripts",
    "_menu_info_help": "Info / Help",
    "_menu_blender_osm": "Blender & OpenStreetMap",
    "_menu_meshes": "Meshes",
    "_menu_map_diagnostics": "Map Diagnostics",
    "_menu_config_status": "Config & Status",
    "_menu_semantic_maps": "Semantic Maps",
    "_edit_blender_paths": "Blender & OpenStreetMap Config",
    "_edit_multiplayer_settings": "Multiplayer Settings",
}


def _audit_path_from_stack() -> str:
    """Derive the menu nav path from the live Python call stack.

    Walks frames outermost-first, collecting any whose function name appears
    in _MENU_FUNC_TITLES or matches the _menu_/_edit_ prefix convention.
    Unknown _menu_* functions get an auto-derived title so new menus appear
    automatically without a manual registration step.
    """
    titles: list[str] = []
    seen: set[str] = set()
    for frame_info in reversed(inspect.stack()):
        name = frame_info.function
        if name in seen:
            continue
        if name in _MENU_FUNC_TITLES:
            titles.append(_MENU_FUNC_TITLES[name])
            seen.add(name)
        elif name.startswith(("_menu_", "_edit_")):
            raw = name.lstrip("_")
            for prefix in ("menu_", "edit_"):
                if raw.startswith(prefix):
                    raw = raw[len(prefix):]
                    break
            titles.append(raw.replace("_", " ").title())
            seen.add(name)
    return " > ".join(titles) if titles else "(top level)"

RUN_DIR = REPO_ROOT / ".run"
ASSET_DIR = REPO_ROOT / "assets"
DEFAULT_MAP = ASSET_DIR / "a3d" / "game_map_y8.a3d"
MAP_SELECTION_DIR = RUN_DIR / "map_selection"
MAP_SELECTION_FILES = {
    "single-player": MAP_SELECTION_DIR / "single-player.json",
    "candidate": MAP_SELECTION_DIR / "candidate.json",
    "current": MAP_SELECTION_DIR / "current.json",
    "asciiid": MAP_SELECTION_DIR / "asciiid-load-proof.json",
}
RUNS_ROOT = REPO_ROOT / "artifacts" / "maintainer" / "watchdog_runs"
_SELECTED_MAP_PATH: Path | None = None
REMOTE_TOPOLOGIES = {"single-vps", "hybrid", "two-machine"}
CURRENT_DEPLOY_TOPOLOGIES = {"hybrid", "two-machine"}
PLAYWRIGHT_DEFAULTS = {
    "PLAYWRIGHT_VIEWPORT": "375x812",
    "PLAYWRIGHT_DURATION": "60",
    "PLAYWRIGHT_BROWSER_ENGINE": "webkit",
    "PLAYWRIGHT_DEVICE": "iPhone 14",
}
PLAYWRIGHT_DEVICE = PLAYWRIGHT_DEFAULTS["PLAYWRIGHT_DEVICE"]
EXIT_POLICY_BLOCKED = 3  # permanent block or unimplemented — do not retry
EXIT_MISSING_ARG = 4     # missing required arg for non-interactive mode — retry with flag
TRUST_AUDIT_DIR = RUN_DIR / "watchdog_trust_audit"
DEFAULT_LOCAL_WEB_PORT = 8080
DEFAULT_LOCAL_WS_PORT = 8080
DEFAULT_LOCAL_MAX_PLAYERS = 5
MIGRATION_SPEC_PATH = "docs/plans/2026-03-22-multiplayer-canonical-spec.md"
LOCAL_SERVER_STATE_PATH = RUN_DIR / "launcher-local-server.json"
LOCAL_HOST_PREFS_PATH = RUN_DIR / "launcher-local-host.json"
TARGET_CONTEXT_PATH = RUN_DIR / "launcher-target-context.json"
TARGET_CONTEXT_CHOICES = {"localhost", "test-vps", "live-vps", "custom"}
RECIPE_ALLOWED_STEP_KINDS = {
    "validate-attach",
    "sleep",
    "send-key",
    "send-sequence",
    "hold-key-begin",
    "hold-key-end",
    "barrier",
}
DIFF_CORPUS_CHOICES = ("gameplay", "watchdog", "launcher", "all")

MENU_ITEMS = [
    ("1", "GAME", "single-player direct; multiplayer is nested"),
    ("2", "ASSET & MAP EDITOR", "map editor, maps, sprites, dev tools"),
    ("3", "CONFIG & STATUS", "health, multiplayer, Blender, tool servers"),
    (">", "COMMAND LINE", "type a command, tab to finish"),
    ("q", "QUIT", "or Ctrl-C"),
]
MENU_NAME_WIDTH = max(len(name) for _, name, _ in MENU_ITEMS)

# ── RainEngine (FL-2790) ──────────────────────────────────────────────────
_rain_engine = _RainEngine(_renderer, MENU_ITEMS)

from launcher_input import InputManager as _InputManager

# InputManager created early; flag_issue_callback patched after _flag_issue_prompt exists.
_input_mgr: _InputManager = _InputManager(
    _renderer, _io_mgr, _rain_engine,
    audit_flag_char=_AUDIT_FLAG_CHAR, audit_mode=_AUDIT_MODE,
)


def _repo_python() -> str:
    return _pipeline.preferred_python()


def _tool_env(*extra_paths: str) -> dict[str, str]:
    env = dict(os.environ)
    parts = [p for p in extra_paths if p]
    existing = env.get("PYTHONPATH", "")
    if existing:
        parts.append(existing)
    if parts:
        env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


def _getch() -> str:
    _sync_audit_mode()
    return _input_mgr._getch()


def _prompt_char(prompt: str = "> ") -> str:
    _sync_audit_mode()
    return _input_mgr.prompt_char(prompt, flag_issue_callback=_flag_issue_prompt)


def _prompt_choice(prompt: str = "> ", default: str = "") -> str:
    _sync_audit_mode()
    return _input_mgr.prompt_choice(prompt, default)


def _prompt_line(prompt: str, default: str = "") -> str:
    _sync_audit_mode()
    return _input_mgr.prompt_line(prompt, default)


def _can_prompt() -> bool:
    _sync_audit_mode()
    return _input_mgr.can_prompt()


def _read_nav_key() -> str:
    """Read a single key, handling ESC sequences for arrow keys."""
    if os.name == "nt":
        import msvcrt
        raw = msvcrt.getwch()
        if raw in ("\x00", "\xe0"):
            code = msvcrt.getwch()
            if code == "H":
                return "\x1b[A"
            if code == "P":
                return "\x1b[B"
        return raw
    import select, termios, tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        try:
            raw = os.read(fd, 1).decode(errors="ignore")
        except OSError:
            return ""
        if raw == "\x1b":
            # Drain the full escape sequence (may be 2 or 3+ bytes: \x1b [ char or \x1b [ digit ~)
            seq = ""
            while select.select([sys.stdin], [], [], 0.05)[0]:
                try:
                    ch = os.read(fd, 1).decode(errors="ignore")
                except OSError:
                    break
                seq += ch
                # Stop after a letter (final byte of CSI sequence) or tilde
                if ch.isalpha() or ch == "~":
                    break
            return f"\x1b{seq}"
        return raw
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _file_picker(start_dir: Path, *, filter_glob: str = "*", title: str = "Select file") -> Path | None:
    """Keyboard-browsable file picker. Returns selected Path or None on cancel."""
    if not _can_prompt():
        return None
    if not start_dir.exists():
        console.print(f"  [yellow]⚠[/yellow]  Directory not found: {start_dir}")
        _pause("  Press Enter to continue.")
        return None
    current = start_dir.resolve()
    cursor = 0
    while True:
        try:
            entries = sorted(
                [p for p in current.iterdir() if p.is_dir() or p.match(filter_glob)],
                key=lambda p: (p.is_file(), p.name.lower()),
            )
        except OSError as exc:
            console.print(f"  [red]x[/red]  Cannot read directory: {exc}")
            _pause("  Press Enter to continue.")
            return None
        if current != current.parent:
            entries = [current.parent] + entries
        if not entries:
            console.print(f"  [yellow]⚠[/yellow]  No entries in {current}")
            _pause("  Press Enter to continue.")
            return None

        cursor = min(cursor, max(0, len(entries) - 1))
        console.clear()
        console.print(f"  [bold]{escape(title)}[/bold]  ({escape(str(current))})")
        console.print("  [dim]↑↓ / jk navigate  Enter select  q cancel[/dim]")
        for i, e in enumerate(entries):
            tag = "/" if e.is_dir() else ""
            name = ".." if e == current.parent else e.name + tag
            line = f"   {escape(name)}" if i != cursor else f"  [reverse]{escape(name)}[/reverse]"
            console.print(line)
        key = _read_nav_key()
        if key in ("q", "\x1b"):
            return None
        elif key in ("\r", "\n"):
            chosen = entries[cursor]
            if chosen.is_dir():
                current = chosen.resolve()
                cursor = 0
            else:
                return chosen
        elif key in ("k", "\x1b[A"):
            cursor = max(0, cursor - 1)
        elif key in ("j", "\x1b[B"):
            cursor = min(len(entries) - 1, cursor + 1)


def _numbered_picker(items: list[Path], title: str = "Select item") -> Path | None:
    """Show a numbered list of Paths and return the selected one, or None.

    FL-3636: replaced numbered list + _prompt_line with _fuzzy_select.
    Users can type to filter or use arrow keys (↑↓/jk) + Enter.
    Legacy numbered-entry fallback preserved if list is small (≤8 items).
    """
    if not _can_prompt():
        return None
    if not items:
        console.print(f"  [yellow]⚠[/yellow]  No items for {title}")
        _pause("  Press Enter to continue.")
        return None
    if len(items) <= 8:
        # Small list: show numbered summary + _fuzzy_select
        console.print(f"  [bold]{title}[/bold]")
        for i, item in enumerate(items, 1):
            label = str(_repo_relative(item)) if item.is_relative_to(REPO_ROOT) else str(item)
            console.print(f"    [{i}] {escape(label)}")
        console.print()
    entries = [
        (str(_repo_relative(p)) if p.is_relative_to(REPO_ROOT) else str(p), p)
        for p in items
    ]
    chosen = _fuzzy_select(
        entries,
        title=title,
        label_fn=lambda e: e[0],
        console=console,
        renderer=_renderer,
    )
    return chosen[1] if chosen else None


def _pause(message: str) -> None:
    _sync_audit_mode()
    _input_mgr.pause(message)


def _write_audit_fl(desc: str, category: str, path: str) -> None:
    """Append a [USER_NOTE][AUDIT] FL entry + overlay row to FAILURE_LOG.md."""
    fl_path = REPO_ROOT / "docs" / "FAILURE_LOG.md"
    date = time.strftime("%Y-%m-%d")
    try:
        text = fl_path.read_text(encoding="utf-8")
    except OSError as exc:
        console.print(f"  [red]\u2717[/red]  Could not read FAILURE_LOG.md: {exc}")
        return
    lines = text.splitlines(keepends=True)
    nums = [int(m) for m in re.findall(r"^### FL-(\d+)", text, re.MULTILINE)]
    fl_id = f"FL-{(max(nums) if nums else 0) + 1}"
    cat_text = category or "(none)"
    entry = (
        f"\n### {fl_id}: [USER_NOTE][AUDIT] {desc} ({date})\n\n"
        f"**Status:** OPEN\n"
        f"**Category:** launcher_ux_audit / user_report\n"
        f"**\u26a0 ADDED DURING LIVE LAUNCHER AUDIT \u2014 REWORD TITLE, "
        f"PICK AREA/SUBSYSTEMS/KINDS, UPDATE OVERLAY BEFORE ACTING**\n\n"
        f"Issue: {desc}\n"
        f"Quick category: {cat_text}\n"
        f"Navigation path at capture: {path}\n"
        f"Captured: {date} via in-launcher audit mode (Ctrl+F)\n\n"
        f"No evidence gathered. Entry was captured during manual launcher walkthrough.\n\n"
        f"**Proof state:** OPEN \u2014 raw user observation {date}; "
        f"not yet classified; no fix attempt.\n\n"
        f"---\n"
    )
    overlay = json.dumps(
        {
            "fl": fl_id,
            "Area": "launcher",
            "Subsystems": ["launcher_ux_audit"],
            "Kinds": ["user_report", "unclassified"],
            "ProofState": "OPEN",
            "ComplaintRefs": [],
            "ComplaintCounterState": (
                f"RAW_USER_NOTE (AUDIT): path={path}; category={cat_text}. "
                "Reword title, set Area/Subsystems/Kinds, update overlay."
            ),
            "CodeRefs": [],
            "TouchedFiles": ["docs/FAILURE_LOG.md"],
        },
        separators=(",", ":"),
    )
    in_block = False
    close_idx = -1
    for i, line in enumerate(lines):
        if line.strip() == "```jsonl":
            in_block = True
            continue
        if in_block and line.strip() == "```":
            close_idx = i
            break
    if close_idx < 0:
        console.print("  [red]\u2717[/red]  FL overlay close marker not found; entry not written.")
        return
    lines.insert(close_idx, overlay + "\n")
    try:
        fl_path.write_text("".join(lines) + entry, encoding="utf-8")
    except OSError as exc:
        console.print(f"  [red]\u2717[/red]  Could not write FAILURE_LOG.md: {exc}")
        return
    console.print(f"  [bold]\u2713[/bold]  {fl_id} logged to FAILURE_LOG.md")


def _flag_issue_prompt() -> None:
    """Ctrl+F issue reporter: category menu + free text -> FL entry."""
    path_str = _audit_path_from_stack()
    console.print()
    console.rule("[bold red]FLAG ISSUE[/bold red]")
    console.print(f"  Path: [bold]{escape(path_str)}[/bold]")
    console.print()
    _cat_options: list[tuple[str, str]] = [("", "— skip —")] + list(_AUDIT_CATEGORIES)
    _cat_chosen = _fuzzy_select(
        _cat_options,
        title="Issue category  (Enter to skip)",
        label_fn=lambda t: t[1],
        console=console,
        renderer=_renderer,
    )
    cat_raw = _cat_chosen[0] if _cat_chosen is not None else ""
    cat_label = _AUDIT_CATEGORIES_MAP.get(cat_raw, "")
    # Re-render the audit UI context so the description prompt isn't on a blank screen
    if _renderer.active and _io_mgr.submenu_buffering:
        console.print()
        console.rule("[bold red]FLAG ISSUE[/bold red]")
        console.print(f"  Path: [bold]{escape(path_str)}[/bold]")
        if cat_label:
            console.print(f"  Category: [bold]{escape(cat_label)}[/bold]")
        console.print()
    else:
        console.print()
    desc = _prompt_line("  Describe the issue", "").strip()
    if not desc:
        console.print("  [dim]No issue recorded (empty description).[/dim]")
        console.rule()
        console.print()
        return
    full_desc = desc
    if cat_label:
        full_desc += f" [{cat_label}]"
    _write_audit_fl(full_desc, cat_label, path_str)
    console.rule()
    console.print()


def _menu_line(text: str, *, suffix_markup: str | None = None) -> None:
    """Print a menu item line, styling [X] key badges as bold red."""
    # Visible labels carry destructive warnings before keypress; later prompts
    # only confirm the already-advertised consequence.
    text = re.sub(r"\s+goto:[^\s]+", "", text).rstrip()
    parts = re.split(r"(\[[a-zA-Z0-9]\])", text)
    styled = ""
    for part in parts:
        if re.match(r"^\[[a-zA-Z0-9]\]$", part):
            styled += f"[bold red]{escape(part)}[/bold red]"
        else:
            styled += escape(part)
    if suffix_markup:
        styled += f" {suffix_markup}"
    console.print(styled)


def _dim_suffix(text: str) -> str:
    """Return escaped dim suffix markup for menu helper text."""
    return f"[dim]{escape(text)}[/dim]"


def _menu_support_badge(support_state: str) -> str | None:
    support_state = support_state.strip().lower()
    if support_state == "non-authoritative":
        return "[bold yellow]⚠ non-authoritative[/bold yellow]"
    if support_state and support_state != "manual-cli-required":
        return f"[dim]{escape(support_state)}[/dim]"
    return None


def _fancy_terminal_ui_enabled() -> bool:
    return _can_prompt() and _ui_fancy.enabled() and not _AUDIT_MODE


def _launcher_altscreen_enabled() -> bool:
    return os.environ.get("ASCIICKER_LAUNCHER_ALT_SCREEN") == "1" and _fancy_terminal_ui_enabled()


def _rain_ui_enabled() -> bool:
    return (
        _can_prompt()
        and not _AUDIT_MODE
        and _rain_engine.rain_ui_enabled()
    )


def _draw_submenu_header(title: str) -> None:
    """Clear screen, render banner, draw rule, and set up scroll region.

    When the unified renderer is active, this starts content buffering:
    subsequent console.print() calls are captured into a StringIO buffer
    and flushed to the renderer's content zone at prompt time.

    Legacy path: clear screen + title rule (no scroll region).
    """
    if _renderer.active:
        # Clear scrollback buffer so prior content doesn't peek above banner
        console.clear()
        # Build 0.7x banner (smaller than root so rain has room) + title rule
        cols = _renderer.cols
        _renderer.set_banner(_cached_submenu_banner_lines(cols))
        # Signal scanline reveal on next _prompt_char
        _rain_engine.submenu_rain["scanline_pending"] = True
        # Start capturing console output into the buffer
        _start_submenu_buffer()
        # Title rule goes into the buffer as first content
        console.rule(f"\u26a0 {title}", style="dim")
        return
    # Legacy path (non-renderer): clear screen + print title rule
    sys.stdout.write("\033[H\033[2J")
    sys.stdout.flush()
    console.rule(f"\u26a0 {title}", style="dim")


def _menu_disabled_line(key: str, label: str, reason: str = "") -> None:
    suffix = f" [dim]{escape(reason)}[/dim]" if reason else ""
    console.print(f"  [bold red]{escape(f'[{key}]')}[/bold red] [dim]{escape(label)}[/dim]{suffix}")


def _planned_launcher_gap(label: str, detail: str) -> int:
    console.print(f"  [yellow]![/yellow]  {label} is planned but not implemented in the scripts launcher.")
    console.print(f"  {detail}")
    console.print(f"  Spec: {MIGRATION_SPEC_PATH}")
    return EXIT_POLICY_BLOCKED


def _migration_plan_lines() -> list[str]:
    tree = _option_tree.option_tree()
    flattened = _option_tree.flatten_options(tree)
    blocked_metadata = [
        f"{item.get('menu_id')}:{item.get('key')} {item.get('label')} - {item.get('failure_surface')}"
        for item in flattened
        if item.get("support_state") in {"planned", "deferred"}
    ]
    backed_targets = [
        f"{item.get('menu_id')}:{item.get('key')} {item.get('label')} - {item.get('command_preview')}"
        for item in flattened
        if item.get("label") in {
            "--commit-all-and-reset",
            "Verify OSM Building",
            "MCP Mount Status",
        }
    ]
    open_script_ux = [
        f"{item.get('menu_id')}:{item.get('key')} {item.get('label')} - {item.get('command_preview')}"
        for item in flattened
        if item.get("script_ux_state") == "open-user-ux"
    ]
    blocked_metadata_output = [f"- {line}" for line in blocked_metadata[:40]] or ["- no planned/deferred metadata; every leaf still requires manual CLI proof"]
    open_script_ux_output = [f"- {line}" for line in open_script_ux[:40]] or ["- none"]
    return [
        "Testing launcher migration plan",
        f"Spec: {MIGRATION_SPEC_PATH}",
        f"Option tree version: {tree.get('version', 'unknown')}",
        "",
        "Current gates:",
        "- Every visible leaf is manual-CLI-required until walked through python3 scripts/launcher.py.",
        "- Do not treat option-tree shape, migration-plan counts, or unit tests as launcher proof.",
        "- Do not treat legacy trust-audit or open-user-ux scripts as proof gates.",
        "- Backed candidate proof, when tested, must use scripts/watchdog_runner.py --mode full --target candidate --commit-all-and-reset.",
        "",
        "Blocked metadata (not proof):",
        *blocked_metadata_output,
        *([f"- ... {len(blocked_metadata) - 40} more"] if len(blocked_metadata) > 40 else []),
        "",
        "Tracked target leaves:",
        *(f"- {line}" for line in backed_targets),
        "",
        "Open script UX leaves:",
        *open_script_ux_output,
        *([f"- ... {len(open_script_ux) - 40} more"] if len(open_script_ux) > 40 else []),
    ]


def _bundle_parity_lines_via_seam() -> list[str]:
    """Bundle parity display lines, delegated to bundle_hash_status seam."""
    try:
        status = _bundle_hash_collect_status()
    except Exception as exc:
        return [f"  bundle parity: unavailable ({exc})"]
    parity_ok = status.get("ok")
    em_dash = "\u2014"
    parity_label = (
        "PASS (all deploy slots match local build)"
        if parity_ok
        else f"FAIL (deploy slot and local build differ {em_dash} re-deploy or rebuild to sync)"
    )
    lines = [
        "  bundle parity: "
        + parity_label
        + f" bundle_hash={','.join(status.get('bundle_hashes') or ['-'])} "
        + f"ids_lock_hash={','.join(status.get('ids_lock_hashes') or ['-'])}"
    ]
    for identity in status.get("identities", []):
        if not isinstance(identity, dict):
            continue
        lines.append(
            "  "
            f"bundle {identity.get('name')}: "
            f"bundle_hash={identity.get('bundle_hash') or '-'} "
            f"ids_lock_hash={identity.get('ids_lock_hash') or '-'} "
            f"path={identity.get('path') or '-'}"
        )
    return lines



def _print_migration_plan(*, pause_on_success: bool = False) -> int:
    _write_stdout_lines(_migration_plan_lines())
    if pause_on_success:
        _pause("  Press Enter to continue.")
    return 0





def _read_json_file(path: Path) -> dict[str, object] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write_json_file(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _target_context_payload(env: dict[str, str]) -> dict[str, str]:
    data = _read_json_file(TARGET_CONTEXT_PATH) or {}
    context = str(data.get("context") or "test-vps")
    if context not in TARGET_CONTEXT_CHOICES:
        context = "test-vps"
    custom_host = str(data.get("custom_host") or "")
    if context == "localhost":
        return {
            "context": context,
            "label": "Localhost",
            "host": "127.0.0.1",
            "base_url": f"http://127.0.0.1:{DEFAULT_LOCAL_WEB_PORT}",
            "ws_server": f"127.0.0.1:{DEFAULT_LOCAL_WS_PORT}",
        }
    if context == "live-vps":
        cfg = _load_slot_config(env, "current")
        return {
            "context": context,
            "label": "Live VPS",
            "host": str(cfg.display_host),
            "base_url": str(cfg.base_url or ""),
            "ws_server": str(cfg.ws_server or ""),
        }
    if context == "custom":
        base_url = _normalize_server_url(custom_host).rstrip("/") if custom_host else ""
        parsed = urlparse(base_url)
        ws_server = parsed.netloc if parsed.netloc else custom_host
        return {
            "context": context,
            "label": "Custom",
            "host": custom_host or "(not set)",
            "base_url": base_url,
            "ws_server": ws_server,
        }
    cfg = _load_slot_config(env, "candidate")
    return {
        "context": "test-vps",
        "label": "Test VPS",
        "host": str(cfg.display_host),
        "base_url": str(cfg.base_url or ""),
        "ws_server": str(cfg.ws_server or ""),
    }


def _target_context_line(env: dict[str, str]) -> str:
    target = _target_context_payload(env)
    label = target["label"]
    if label == "candidate":
        label = "candidate (staging)"
    elif label == "current":
        label = "current (live)"
    return (
        f"selected target: {label} "
        f"host={target['host'] or '-'} "
        f"base={target['base_url'] or '-'} "
        f"ws={target['ws_server'] or '-'}"
    )


def _switch_target_context(args: argparse.Namespace | None = None, *, pause_on_success: bool = False) -> int:
    selected = getattr(args, "target_context", None) if args is not None else None
    custom_host = getattr(args, "target_host", None) if args is not None else None
    if not selected and _can_prompt():
        console.print("  [l] Localhost")
        console.print("  [t] Test VPS")
        console.print("  [v] Live VPS")
        console.print("  [c] Custom")
        choice = _prompt_char("> ")
        selected = {"l": "localhost", "t": "test-vps", "v": "live-vps", "c": "custom"}.get(choice)
    if not selected:
        console.print("  [red]✗[/red]  --target-context is required. Use one of: localhost, test-vps, live-vps, custom")
        return 1
    selected = str(selected)
    if selected not in TARGET_CONTEXT_CHOICES:
        console.print(f"  [red]✗[/red]  Unsupported target context: {selected}. Valid: localhost, test-vps, live-vps, custom")
        return 1
    if selected == "custom" and not custom_host and _can_prompt():
        custom_host = _prompt_line("  Custom host or URL (e.g. 192.168.1.5:8080)", "")
    if selected == "custom" and not custom_host:
        console.print("  [red]✗[/red]  custom target requires --target-host (e.g. --target-host 192.168.1.5:8080)")
        return 1
    _write_json_file(
        TARGET_CONTEXT_PATH,
        {
            "context": selected,
            "custom_host": custom_host or "",
            "written_by": "scripts/launcher.py",
            "mtime": int(time.time()),
        },
    )
    console.print(f"  [green]✓[/green]  Target context saved: {TARGET_CONTEXT_PATH}")
    console.print("  " + _target_context_line(_senv.load()))
    if pause_on_success:
        _pause("  Press Enter to continue.")
    return 0


def _topology_value(env: dict[str, str]) -> str:
    return env.get("AK_MP_SERVER_TOPOLOGY_TYPE", "").strip() or "none"


def _parse_viewport(raw: str) -> tuple[int, int] | None:
    text = raw.strip().lower()
    if "x" not in text:
        return None
    left, right = text.split("x", 1)
    try:
        width = int(left)
        height = int(right)
    except ValueError:
        return None
    if width <= 0 or height <= 0:
        return None
    return width, height


_SAFE_RUN_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,200}$")


def _run_summary_path(run_id: str) -> Path:
    if not _SAFE_RUN_ID_RE.match(run_id):
        raise ValueError(f"Invalid run_id: {run_id!r}")
    if run_id.startswith("failed-"):
        return RUNS_ROOT / f"{run_id}.json"
    return RUNS_ROOT / run_id / "summary.json"


def _load_summary_file(summary_path: Path) -> dict[str, object] | None:
    """Load a summary JSON file through the shared seam (FL-2787)."""
    return _load_summary_file_shared(summary_path)


def _iter_run_summary_records() -> list[tuple[str, Path, dict[str, object]]]:
    """Iterate run records through the shared seam (FL-2787)."""
    return _iter_run_summary_records_shared(RUNS_ROOT)





def _run_record_label(record: tuple[str, Path, dict[str, object]]) -> str:
    """Format a run record label through the shared seam (FL-2787)."""
    return _run_record_label_shared(record, REPO_ROOT)


def _choose_run_id(default: str | None = None) -> str | None:
    records = _iter_run_summary_records_shared(RUNS_ROOT)
    if not records:
        console.print("  [yellow]⚠[/yellow]  No watchdog run summaries found. Run a test first (Run Ops → Full Run).")
        return default

    def _label(r: tuple) -> str:
        marker = " [latest]" if r is records[0] else ""
        return _run_record_label_shared(r, REPO_ROOT) + marker

    def _path(r: tuple) -> "Path | None":
        summary_path = r[1]
        run_dir = summary_path.parent
        return run_dir if run_dir.is_dir() and run_dir != RUNS_ROOT else summary_path

    default_item = next((r for r in records if r[0] == default), None) if default else None

    chosen = _fuzzy_select(
        records,
        title="Select watchdog run",
        label_fn=_label,
        path_fn=_path,
        default=default_item,
        console=console,
        renderer=_renderer,
    )
    return chosen[0] if chosen is not None else None


def _read_run_summary(run_id: str) -> dict[str, object] | None:
    """Read a run summary through the shared seam (FL-2787)."""
    return _read_run_summary_shared(run_id, RUNS_ROOT)


def _path_from_summary(value: object) -> Path | None:
    """Extract a Path from a summary value through the shared seam (FL-2787)."""
    return _path_from_summary_shared(value)


def _watchdog_artifact_status(summary: dict[str, object] | None) -> dict[str, object]:
    """Classify artifact status through the shared seam (FL-2787)."""
    return _watchdog_artifact_status_shared(summary, RUNS_ROOT)


def _mobile_run_id(prefix: str) -> str:
    return f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}"


def _seed_playwright_defaults(env: dict[str, str]) -> dict[str, str]:
    seeded = dict(env)
    for key, value in PLAYWRIGHT_DEFAULTS.items():
        if not seeded.get(key, "").strip():
            seeded[key] = value
    return seeded


def _playwright_browser_install_state() -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["npx", "playwright", "install", "--dry-run"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"playwright browser probe failed: {exc}"
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()[:1]
        return False, detail[0] if detail else "playwright browser probe failed"
    install_locations: list[Path] = []
    for line in (result.stdout or "").splitlines():
        if "Install location:" not in line:
            continue
        location = line.split("Install location:", 1)[1].strip()
        if location:
            install_locations.append(Path(location).expanduser())
    if not install_locations:
        return False, "playwright browser install state unavailable"
    missing = [path for path in install_locations if not path.exists()]
    if missing:
        return False, f"missing browser install(s): {', '.join(str(path) for path in missing)}"
    return True, f"{len(install_locations)} browser install(s) present"


def _print_exception(exc: Exception) -> None:
    console.print(f"  [red]ERROR:[/red] {escape(str(exc))}")


def _default_join_server_url(env: dict[str, str] | None = None) -> str:
    cfg = _load_slot_config(env or _senv.load(), "current").as_dict()
    return str(cfg.get("base_url") or "")


def _default_url_scheme(host: str) -> str:
    if host.startswith(("localhost", "127.", "0.0.0.0", "[")):
        return "http"
    if ":" in host and not host.startswith("["):
        return "http"
    return "https"


def _normalize_server_url(raw_url: str) -> str:
    text = raw_url.strip()
    if not text:
        raise ValueError("Server URL is required.")
    if re.search(r"\s", text):
        raise ValueError("Server URL must not contain whitespace.")
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", text):
        scheme = _default_url_scheme(text)
        text = f"{scheme}://{text}"
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Server URL scheme must be http or https.")
    if not parsed.netloc:
        raise ValueError("Server URL must include a host.")
    path = parsed.path or "/"
    return urlunparse((parsed.scheme, parsed.netloc, path, "", parsed.query, ""))


def _join_server_url(raw_url: str | None, *, open_browser: bool = True) -> int:
    chosen = raw_url or ""
    if not chosen and _can_prompt():
        chosen = _prompt_line("  Server URL (e.g. https://play.example.com)", _default_join_server_url())
    if not chosen:
        chosen = _default_join_server_url()
    try:
        url = _normalize_server_url(chosen)
    except ValueError as exc:
        console.print(f"  [red]✗[/red]  {exc}")
        console.print("  Use: python3 scripts/launcher.py --action join-server-url --server-url <url>")
        return 1

    _print_copyable_command(
        [_repo_python(), "scripts/launcher.py", "--action", "join-server-url", "--server-url", url],
        cwd=REPO_ROOT,
    )
    if not open_browser:
        console.print(url, markup=False, highlight=False)
        return 0
    try:
        opened = webbrowser.open(url)
    except webbrowser.Error as exc:
        console.print(f"  [yellow]⚠[/yellow]  Browser open failed: {exc}")
        console.print(f"  Visit: {escape(url)}")
        return 1
    if not opened:
        console.print(f"  [yellow]⚠[/yellow]  Browser not opened automatically. Visit: {escape(url)}")
        return 1
    console.print(f"  [green]✓[/green]  Opened {escape(url)}")
    return 0


def _local_server_state() -> dict[str, object] | None:
    data = _read_json_file(LOCAL_SERVER_STATE_PATH)
    if not data:
        return None
    try:
        pid = int(data.get("pid", 0))
    except (TypeError, ValueError):
        pid = 0
    if pid <= 0 or not _pid_exists(pid):
        return None
    return data


def _local_lan_hosts() -> list[str]:
    hosts: list[str] = []
    try:
        name = socket.gethostname()
        for item in socket.getaddrinfo(name, None, socket.AF_INET, socket.SOCK_STREAM):
            host = item[4][0]
            if host and not host.startswith("127.") and host not in hosts:
                hosts.append(host)
    except OSError:
        pass
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            host = sock.getsockname()[0]
            if host and not host.startswith("127.") and host not in hosts:
                hosts.append(host)
        finally:
            sock.close()
    except OSError:
        pass
    return hosts


def _local_server_urls(state: dict[str, object]) -> list[str]:
    join_url = str(state.get("join_url") or "").strip()
    try:
        parsed = urlparse(_normalize_server_url(join_url)) if join_url else urlparse(f"http://127.0.0.1:{DEFAULT_LOCAL_WEB_PORT}/")
    except ValueError:
        parsed = urlparse(f"http://127.0.0.1:{DEFAULT_LOCAL_WEB_PORT}/")
    port = parsed.port or DEFAULT_LOCAL_WEB_PORT
    urls = [f"http://127.0.0.1:{port}/"]
    for host in _local_lan_hosts():
        url = f"http://{host}:{port}/"
        if url not in urls:
            urls.append(url)
    return urls


def _open_local_lan_server(*, open_browser: bool = True, pause_on_success: bool = False) -> int:
    state = _local_server_state()
    if state is None:
        console.print("  [red]✗[/red]  No launcher-owned local server is running. Start one via GAME → Host → Host Local.")
        console.print(f"  Start one: {_repo_python()} scripts/launcher.py --action local-play-with-friends --map assets/a3d/game_map_y8.a3d --port {DEFAULT_LOCAL_WEB_PORT}")
        return 1
    urls = _local_server_urls(state)
    console.print(f"  local server pid: {state.get('pid')}")
    console.print(f"  map: {state.get('map', '-')}")
    console.print(f"  log: {state.get('log', '-')}")
    console.print("  Open from this machine:")
    console.print(f"    {urls[0]}", markup=False, highlight=False)
    if len(urls) > 1:
        console.print("  Open from another LAN device:")
        for url in urls[1:]:
            console.print(f"    {url}", markup=False, highlight=False)
    else:
        console.print("  [yellow]⚠[/yellow]  No non-loopback LAN address was detected; use the local URL from another terminal on this machine.")
    _print_copyable_command([_repo_python(), "scripts/launcher.py", "--action", "join-server-url", "--server-url", urls[0]], cwd=REPO_ROOT)
    if open_browser:
        webbrowser.open(urls[1] if len(urls) > 1 else urls[0])
    if pause_on_success:
        _pause("  Press Enter to continue.")
    return 0


def _env_overrides(env: dict[str, str] | None) -> dict[str, str]:
    if env is None:
        return {}
    overrides: dict[str, str] = {}
    for key, value in env.items():
        if os.environ.get(key) != value:
            overrides[key] = value
    return overrides


def _should_attach_subprocess_to_terminal(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
) -> bool:
    """Return True when captured ScrollView output would hide interactive prompts.

    watchdog_runner.py can emit input() prompts without trailing newlines.
    Those prompts do not surface through ScrollView's readline()-based capture, so
    interactive watchdog runs need a real attached terminal instead.
    """
    normalized = [str(part) for part in args]
    tty_scripts = ("watchdog_runner.py", "xp_uv_body_viewer.py", "xp_anim_viewer.py",
                   "source_layer_contract_viewer.py", "glyph_morphology_browser.py",
                   "glyph_families_viewer.py")
    if not any(script in part for script in tty_scripts for part in normalized):
        return False
    if any("watchdog_runner.py" in part for part in normalized) and ("--json" in normalized or "--json-only" in normalized):
        return False
    effective_env = os.environ.copy()
    if env:
        effective_env.update(env)
    return effective_env.get("WATCHDOG_NON_INTERACTIVE") != "1"


def _run_attached_terminal(
    args: list[str],
    *,
    cwd: Path | str | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess:
    """Run an interactive child against the operator's real terminal."""
    try:
        with open("/dev/tty", "rb", buffering=0) as tty_in, open("/dev/tty", "wb", buffering=0) as tty_out:
            return subprocess.run(
                args,
                cwd=str(cwd) if cwd else None,
                env=env,
                timeout=timeout,
                stdin=tty_in,
                stdout=tty_out,
                stderr=tty_out,
            )
    except OSError:
        return subprocess.run(args, cwd=str(cwd) if cwd else None, env=env, timeout=timeout)


def _resolved_shell_command(
    args: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> str:
    command = shlex.join([str(part) for part in args])
    overrides = _env_overrides(env)
    if overrides:
        prefix = " ".join(f"{key}={shlex.quote(value)}" for key, value in sorted(overrides.items()))
        command = f"{prefix} {command}"
    if cwd is not None:
        command = f"cd {shlex.quote(str(cwd))} && {command}"
    return command


def _print_copyable_command(
    args: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> str:
    command = _resolved_shell_command(args, cwd=cwd, env=env)
    console.print("  [dim]Copy command:[/dim]")
    console.print(f"  {command}", markup=False, highlight=False)
    return command


# ---------------------------------------------------------------------------
# Endpoint health registry — scripts known to be hard-deleted but still wired
# from various menu paths. _run_command consults this so a stale invocation
# fails visibly with a clear message instead of a raw FileNotFoundError.
# Goal: every launcher endpoint either works, or is visibly broken at the
# point of invocation. Add a row here when you delete a backing script;
# remove it once a replacement is wired everywhere it was referenced.
# ---------------------------------------------------------------------------
_DELETED_SCRIPTS: dict[str, str] = {
    "scripts/multiplayer_visual_watchdog.js":
        "Hard-deleted. Visual watchdog superseded by .run/watchdog/* artifacts; no CLI replacement yet.",
    "scripts/promote_candidate_to_current.py":
        "Hard-deleted. Promote-to-current flow pending re-wiring.",
    "scripts/watchdog_run_canonical.py":
        "Hard-deleted. Replaced by scripts/watchdog_runner.py (Phase 5 front door).",
    "scripts/watchdog_trust_audit.py":
        "Hard-deleted. Trust-audit CLI pending re-wiring.",
    "scripts/wrun.py":
        "Hard-deleted. wrun flatten logic was inlined elsewhere; this path is dead.",
}


def _missing_deleted_script(args: list[str]) -> tuple[str, str] | None:
    """Return (script_path, reason) if args invoke a known-deleted script, else None."""
    for arg in args:
        if not isinstance(arg, str):
            continue
        for deleted, reason in _DELETED_SCRIPTS.items():
            if arg == deleted or arg.endswith("/" + deleted) or arg.endswith(deleted):
                return deleted, reason
    return None


def _run_command(
    args: list[str],
    *,
    label: str = "",
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    pause_on_success: bool = False,
    timeout: float | None = None,
) -> int:
    # Fail fast on known-deleted backing scripts so stale menu wiring does
    # not surface as a raw FileNotFoundError. The launcher endpoint is then
    # visibly broken (clear reason printed) rather than silently broken.
    _deleted = _missing_deleted_script(args)
    if _deleted is not None:
        _path, _reason = _deleted
        console.print(f"  [red]x[/red]  endpoint backing script is missing: [bold]{_path}[/bold]")
        console.print(f"  [dim]{_reason}[/dim]")
        if sys.stdout.isatty():
            _pause("  Press Enter to continue.")
        return 1
    # Renderer/capture ownership lives here, not in option_tree metadata.
    if _AUDIT_MODE:
        cmd_preview = " ".join(str(a) for a in args)
        console.print(f"[AUDIT DRY-RUN] Would execute: {cmd_preview}")
        return 0
    _print_copyable_command(args, cwd=cwd, env=env)
    if label:
        console.print(f"  [dim]Running {label}...[/dim]")

    attach_terminal = _should_attach_subprocess_to_terminal(args, env=env)

    # Unified renderer path: capture output in ScrollView (R4/R5/R6)
    if _renderer.active:
        if attach_terminal:
            if _io_mgr.submenu_buffering:
                _flush_and_render(f"  Running {label}..." if label else "")
            try:
                with _renderer.paused():
                    # FL-3667: echo the resolved command to the real terminal
                    # (main screen, not altscreen) so it appears in scrollback
                    # before and after the subprocess output.
                    _cmd_str = _resolved_shell_command(args, cwd=cwd, env=env)
                    sys.stdout.write(f"  $ {_cmd_str}\n")
                    sys.stdout.flush()
                    result = _run_attached_terminal(args, cwd=cwd, env=env, timeout=timeout)
            except subprocess.TimeoutExpired:
                console.print(f"  [yellow]\u26a0[/yellow]  timed out after {timeout}s")
                _pause("  Press Enter to continue.")
                return 1
            if result.returncode != 0:
                console.print(f"  [yellow]\u26a0[/yellow]  exited with code {result.returncode}")
                _pause("  Press Enter to continue.")
            elif pause_on_success:
                _pause("  Press Enter to continue.")
            return result.returncode
        # Flush any buffered console output so "Copy command" line is visible
        # briefly before ScrollView takes over the content zone.
        if _io_mgr.submenu_buffering:
            _flush_and_render(f"  Running {label}..." if label else "")
        sv = _ScrollView(_renderer)
        try:
            rc = sv.run_captured(
                args,
                cwd=str(cwd) if cwd else None,
                env=env,
                timeout=timeout,
            )
        except Exception:
            _renderer.set_status("  subprocess error — Enter to continue")
            _renderer.render()
            _renderer.wait_for_key()
            return 1
        # Interactive scroll through output
        if sv.line_count > 0:
            sv.scroll_loop()
        if rc != 0:
            _renderer.set_status(f"  exited with code {rc} — Enter to continue")
            _renderer.render()
            _renderer.wait_for_key()
        elif pause_on_success:
            _renderer.wait_for_key()
        return rc

    # Fallback: no renderer/panel active (e.g., non-TTY, CI, agent mode)
    try:
        if attach_terminal:
            result = _run_attached_terminal(args, cwd=cwd, env=env, timeout=timeout)
        elif pause_on_success and sys.stdout.isatty():
            # Capture output for paging so long data-viewer output doesn't
            # scroll off screen before the user can read it (P1 finding).
            result = subprocess.run(
                args,
                cwd=str(cwd) if cwd else None,
                env=env,
                timeout=timeout,
                capture_output=True,
                text=True,
                errors="replace",
            )
            output = (result.stdout or "") + (result.stderr or "")
            lines = output.splitlines()
            if len(lines) >= 20:
                import pydoc
                pydoc.pager(output)
            elif output:
                sys.stdout.write(output)
        else:
            result = subprocess.run(args, cwd=str(cwd) if cwd else None, env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        console.print(f"  [yellow]\u26a0[/yellow]  timed out after {timeout}s")
        _pause("  Press Enter to continue.")
        return 1
    if result.returncode != 0:
        console.print(f"  [yellow]\u26a0[/yellow]  exited with code {result.returncode}")
        _pause("  Press Enter to continue.")
    elif pause_on_success:
        _pause("  Press Enter to continue.")
    return result.returncode


def _watchdog_run_command(
    env: dict[str, str],
    *,
    mode: str,
    slot: str,
    controller_mode: str | None = None,
    recipe_name: str | None = None,
    commits: str | None = None,
    tmp_clone_source_policy: str | None = None,
) -> list[str]:
    cfg = _load_slot_config(env, slot).as_dict()
    cmd = [
        _repo_python(),
        "scripts/watchdog_runner.py",
        "--run-label",
        "passive",
        "--mode",
        mode,
        "--target",
        slot,
    ]
    if cfg["ssh_target"]:
        cmd.extend(["--ssh-target", str(cfg["ssh_target"])])
    if cfg["base_url"]:
        cmd.extend(["--base-url", str(cfg["base_url"])])
    if cfg["ws_server"]:
        cmd.extend(["--ws-server", str(cfg["ws_server"])])
    if controller_mode:
        cmd.extend(["--controller-mode", controller_mode])
    if recipe_name:
        cmd.extend(["--controller-recipe", recipe_name])
    if commits:
        cmd.extend(["--commits", commits])
    if tmp_clone_source_policy:
        cmd.extend(["--tmp-clone-source-policy", tmp_clone_source_policy])
    return cmd


_LOCAL_WATCHDOG_PROVENANCE_BANNER = (
    "\u26a0 PROVENANCE WARNING: This watchdog run was invoked directly, not through\n"
    "  watchdog_runner.py. Results have no verifiable relationship to committed\n"
    "  code and cannot be cited as proof evidence. For proof-grade runs, use the\n"
    "  canonical launcher path."
)


def _print_local_watchdog_provenance_warning(*, position: str = "start") -> None:
    """Print the non-canonical provenance disclaimer banner.

    Called at the START and END of every direct-invocation (non-canonical)
    local watchdog run so operators cannot mistake the output for proof-grade
    evidence.  RQ-050.
    """
    style = "bold yellow"
    if position == "start":
        console.rule(f"[{style}]\u26a0 NON-CANONICAL RUN[/{style}]")
    console.print(f"  [{style}]{_LOCAL_WATCHDOG_PROVENANCE_BANNER}[/{style}]")
    console.rule(style=style)
    console.print()


def _local_watchdog_command(env: dict[str, str]) -> list[str]:
    candidate = _load_slot_config(env, "candidate").as_dict()
    port = int(candidate["port"])
    run_id = _mobile_run_id("watchdog-local")
    out_dir = RUNS_ROOT / run_id / "raw"
    return [
        "node",
        "scripts/multiplayer_visual_watchdog.js",
        "--headed",
        "--run-id",
        run_id,
        "--out-dir",
        str(out_dir),
        "--base-url",
        f"http://127.0.0.1:{port}",
        "--ws-server",
        f"127.0.0.1:{port}",
        "--expected-slot-manifest",
        str(REPO_ROOT / ".web" / "slot_manifest.json"),
        "--expected-slot-name",
        "candidate",
        "--expected-machine-role",
        "candidate",
        "--allow-local-target",
        "--skip-machine-health",
    ]


def _mobile_playwright_command(
    env: dict[str, str],
    *,
    seed_defaults: bool,
) -> tuple[list[str] | None, dict[str, str], str | None, Path | None]:
    local_env = dict(env)
    if seed_defaults:
        local_env = _seed_playwright_defaults(local_env)
    viewport = local_env.get("PLAYWRIGHT_VIEWPORT", "").strip()
    duration = local_env.get("PLAYWRIGHT_DURATION", "").strip()
    engine = local_env.get("PLAYWRIGHT_BROWSER_ENGINE", "").strip() or PLAYWRIGHT_DEFAULTS["PLAYWRIGHT_BROWSER_ENGINE"]
    device = local_env.get("PLAYWRIGHT_DEVICE", "").strip() or PLAYWRIGHT_DEFAULTS["PLAYWRIGHT_DEVICE"]
    if not viewport:
        return None, local_env, "PLAYWRIGHT_VIEWPORT: required", None
    if not duration:
        return None, local_env, "PLAYWRIGHT_DURATION: required", None
    for key, value in (
        ("PLAYWRIGHT_VIEWPORT", viewport),
        ("PLAYWRIGHT_DURATION", duration),
        ("PLAYWRIGHT_BROWSER_ENGINE", engine),
        ("PLAYWRIGHT_DEVICE", device),
    ):
        err = _senv.validate_field(key, value)
        if err:
            return None, local_env, f"{key}: {err}", None
    size = _parse_viewport(viewport)
    if not size:
        return None, local_env, "PLAYWRIGHT_VIEWPORT: must be WIDTHxHEIGHT", None
    try:
        seconds = int(duration)
    except ValueError:
        return None, local_env, "PLAYWRIGHT_DURATION: must be a positive integer number of seconds", None
    width, height = size
    candidate = _load_slot_config(local_env, "candidate").as_dict()
    if not candidate["base_url"] or not candidate["ws_server"]:
        return None, local_env, "Mobile test target not configured. Go to Settings -> Multiplayer Settings and set your candidate server URL.", None
    run_id = _mobile_run_id("mobile-playwright")
    out_dir = RUNS_ROOT / run_id / "raw"
    cmd = [
        "node",
        "scripts/multiplayer_visual_watchdog.js",
        "--headed",
        "--run-id",
        run_id,
        "--out-dir",
        str(out_dir),
        "--base-url",
        str(candidate["base_url"]),
        "--ws-server",
        str(candidate["ws_server"]),
        "--mobile-device",
        device,
        "--browser-engine",
        engine,
        "--window-width",
        str(width + 80),
        "--window-height",
        str(height + 140),
        "--hold-open-ms",
        str(seconds * 1000),
    ]
    if candidate["ssh_target"]:
        cmd.extend(["--ssh-target", str(candidate["ssh_target"])])
    return cmd, local_env, None, out_dir


def _recipe_record_command(run_id: str, recipe_name: str) -> list[str]:
    return [
        _repo_python(),
        "scripts/watchdog/recipe_store.py",
        "capture-from-run",
        run_id,
        "--recipe-name",
        recipe_name,
    ]


def _recipe_capture_command(
    run_id: str,
    recipe_name: str,
    *,
    tab: str = "both",
    from_rel_s: float | None = None,
    to_rel_s: float | None = None,
    description: str = "",
    related_fl: list[str] | None = None,
) -> list[str]:
    cmd = _recipe_record_command(run_id, recipe_name)
    cmd.extend(["--tab", tab])
    if from_rel_s is not None:
        cmd.extend(["--from-rel-s", str(from_rel_s)])
    if to_rel_s is not None:
        cmd.extend(["--to-rel-s", str(to_rel_s)])
    if description:
        cmd.extend(["--description", description])
    for fl_id in related_fl or []:
        cmd.extend(["--related-fl", fl_id])
    return cmd


def _recipe_dry_run_command(recipe_name: str) -> list[str]:
    return [
        _repo_python(),
        "scripts/watchdog_runner.py",
        "--mode",
        "watchdog-only",
        "--target",
        "candidate",
        "--controller-mode",
        "recipe",
        "--controller-recipe",
        recipe_name,
        "--dry-run",
    ]


def _recipe_followup_front_door_command(
    env: dict[str, str],
    *,
    mode: str = "watchdog-only",
    diff_corpus: str | None = "gameplay",
) -> list[str]:
    cmd = _watchdog_run_command(
        env,
        mode=mode,
        slot="candidate",
        controller_mode="manual",
    )
    cmd.append("--followup-repeat-with-derived-recipe")
    return _append_intent_diff_flags(cmd, diff_corpus)


def _append_intent_diff_flags(cmd: list[str], diff_corpus: str | None) -> list[str]:
    corpus = (diff_corpus or "").strip().lower()
    if corpus and corpus in DIFF_CORPUS_CHOICES and corpus != "all":
        cmd.extend(["--intent-diff-corpus", corpus, "--intent-diff-mode", "latest_relevant"])
    elif corpus == "all":
        cmd.extend(["--intent-diff-corpus", "all", "--intent-diff-mode", "between_runs"])
    return cmd


def _exact_repeat_command(run_id: str | None, *, diff_corpus: str | None = None) -> list[str]:
    cmd = [_repo_python(), "scripts/watchdog_runner.py"]
    clean_run_id = (run_id or "").strip()
    if clean_run_id:
        cmd.extend(["--repeat-exact-run", clean_run_id])
    else:
        cmd.append("--repeat-exact-last")
    return _append_intent_diff_flags(cmd, diff_corpus)


def _recipe_repeat_command(
    env: dict[str, str],
    recipe_name: str,
    *,
    mode: str,
    hold_open_ms: int,
    diff_corpus: str | None = "gameplay",
) -> list[str]:
    if mode == "local":
        cmd = _local_watchdog_command(env)
        cmd.extend(["--controller-mode", "recipe", "--controller-recipe-name", recipe_name, "--hold-open-ms", str(hold_open_ms)])
        return cmd
    canonical_mode = "full" if mode == "full" else "watchdog-only"
    cmd = _watchdog_run_command(
        env,
        mode=canonical_mode,
        slot="candidate",
        controller_mode="recipe",
        recipe_name=recipe_name,
    )
    cmd.extend(["--controller-hold-open-ms", str(hold_open_ms)])
    return _append_intent_diff_flags(cmd, diff_corpus)


def _summary_rerun_command(summary: dict[str, object], *, diff_corpus: str | None = None) -> list[str] | None:
    run_id = str(summary.get("run_id") or "").strip()
    if run_id:
        return _exact_repeat_command(run_id, diff_corpus=diff_corpus)
    mode = str(summary.get("mode") or "").strip()
    ssh_target = str(summary.get("ssh_target") or "").strip()
    base_url = str(summary.get("base_url") or "").strip()
    ws_server = str(summary.get("ws_server") or "").strip()
    run_label = str(summary.get("run_label") or "").strip() or "passive"
    target = str(
        summary.get("target")
        or summary.get("slot")
        or summary.get("target_slot")
        or ""
    ).strip()
    if target not in {"candidate", "current"}:
        return None
    if not mode or not ssh_target or not base_url or not ws_server:
        return None
    cmd = [
        _repo_python(),
        "scripts/watchdog_runner.py",
        "--run-label",
        run_label,
        "--mode",
        mode,
        "--target",
        target,
    ]
    cmd.extend(["--ssh-target", ssh_target])
    cmd.extend(["--base-url", base_url])
    cmd.extend(["--ws-server", ws_server])
    controller_mode = str(summary.get("controller_mode") or "").strip()
    if controller_mode and controller_mode != "off":
        cmd.extend(["--controller-mode", controller_mode])
    controller_recipe = str(summary.get("controller_recipe") or "").strip()
    if controller_recipe:
        cmd.extend(["--controller-recipe", controller_recipe])
    return _append_intent_diff_flags(cmd, diff_corpus)


def _validated_fl_id(raw: str | None) -> str | None:
    fl_id = (raw or "").strip().upper()
    if not fl_id:
        console.print("  [dim](cancelled — enter a bug ID, e.g. FL-1173)[/dim]")
        return None
    if not re.fullmatch(r"FL-\d{3,5}", fl_id):
        console.print("  [red]✗[/red]  FL id must look like FL-NNN (e.g. FL-1173).")
        return None
    return fl_id


def _load_fl_entries() -> list[tuple[str, str]]:
    """Return [(fl_id, short_title), ...] from FAILURE_LOG.md, newest-first."""
    log_path = REPO_ROOT / "docs" / "FAILURE_LOG.md"
    try:
        text = log_path.read_text(encoding="utf-8")
    except OSError:
        return []
    entries: list[tuple[str, str]] = []
    for line in text.splitlines():
        m = re.match(r"^### (FL-\d+): (.+)$", line)
        if not m:
            continue
        fl_id = m.group(1)
        # Strip status tags ([OPEN], [RESOLVED], etc.) and trailing date
        title = re.sub(r"\[[A-Z_]+\]\s*", "", m.group(2))
        title = re.sub(r"\s*\(\d{4}-\d{2}-\d{2}\)\s*$", "", title).strip()
        entries.append((fl_id, title))
    entries.reverse()  # newest-first
    return entries


def _choose_fl_id(suggested: list[str] | None = None) -> str | None:
    """Fuzzy-selector FL ID picker. Returns FL ID string or None if cancelled."""
    entries = _load_fl_entries()
    if not entries:
        # Log unreadable — fall back to manual entry
        return _validated_fl_id(_prompt_line("  FL id (blank to cancel, e.g. FL-1173)", ""))

    default_entry: tuple[str, str] | None = None
    if suggested:
        for fl_id in suggested:
            match = next((e for e in entries if e[0].upper() == fl_id.upper()), None)
            if match:
                default_entry = match
                break

    chosen = _fuzzy_select(
        entries,
        title="Select FL entry",
        label_fn=lambda e: f"{e[0]}  {e[1]}",
        path_fn=lambda e: REPO_ROOT / "docs" / "FAILURE_LOG.md",
        default=default_entry,
        console=console,
        renderer=_renderer,
    )
    return chosen[0] if chosen is not None else None


def _choose_failure_log_entry(*, title: str = "Browse Failure Log") -> tuple[str, str] | None:
    """Fuzzy-selector Failure Log browser. Returns (FL id, title), newest-first."""
    entries = _load_fl_entries()
    if not entries:
        console.print("  [red]✗[/red]  Could not read docs/FAILURE_LOG.md")
        return None
    return _fuzzy_select(
        entries,
        title=title,
        label_fn=lambda e: f"{e[0]}  {e[1]}",
        path_fn=lambda e: REPO_ROOT / "docs" / "FAILURE_LOG.md",
        console=console,
        renderer=_renderer,
    )


def _recipe_name_arg(recipe_name: str | None) -> str | None:
    name = (recipe_name or "").strip()
    if not name:
        console.print("  [red]✗[/red]  --recipe-name is required.")
        return None
    return name


def _is_nonnegative_int(value: object) -> bool:
    try:
        return int(value) >= 0
    except (TypeError, ValueError):
        return False


def _valid_recipe_tab(value: object) -> bool:
    try:
        return int(value) in {1, 2}
    except (TypeError, ValueError):
        return False


def _validate_recipe_structure(recipe: dict[str, object]) -> list[str]:
    errors: list[str] = []
    steps = recipe.get("steps")
    if not isinstance(steps, list) or not steps:
        return ["Recipe steps[] must be a non-empty list."]

    active_holds: dict[str, tuple[int, int]] = {}
    for index, step in enumerate(steps, 1):
        context = f"step {index}"
        if not isinstance(step, dict):
            errors.append(f"{context}: must be an object")
            continue
        if "action" in step:
            errors.append(f"{context}: forbidden action field; recipes may not call verifier/debug owners")
        kind = str(step.get("kind") or "").strip()
        if kind not in RECIPE_ALLOWED_STEP_KINDS:
            errors.append(f"{context}: unsupported recipe step kind {kind!r}")
            continue

        if "tab" in step and not _valid_recipe_tab(step.get("tab")):
            errors.append(f"{context}: tab must be 1 or 2")

        if kind == "sleep":
            if not _is_nonnegative_int(step.get("duration_ms", 0)):
                errors.append(f"{context}: duration_ms must be a non-negative integer")
        elif kind == "send-key":
            if not isinstance(step.get("key"), str) or not step["key"].strip():
                errors.append(f"{context}: key is required")
            if not _is_nonnegative_int(step.get("duration_ms", 0)):
                errors.append(f"{context}: duration_ms must be a non-negative integer")
        elif kind == "send-sequence":
            sequence = step.get("sequence")
            if not isinstance(sequence, str) or not sequence.strip():
                errors.append(f"{context}: sequence is required")
        elif kind == "hold-key-begin":
            token = str(step.get("token") or "").strip()
            if not isinstance(step.get("key"), str) or not step["key"].strip():
                errors.append(f"{context}: key is required")
            if not token:
                errors.append(f"{context}: token is required")
            elif token in active_holds:
                prior_index, prior_tab = active_holds[token]
                errors.append(f"{context}: duplicate active hold token {token!r}; already held at step {prior_index} on tab {prior_tab}")
            else:
                tab = int(step.get("tab") or 1)
                if tab in {1, 2}:
                    active_holds[token] = (index, tab)
        elif kind == "hold-key-end":
            token = str(step.get("token") or "").strip()
            if not token:
                errors.append(f"{context}: token is required")
            elif token not in active_holds:
                errors.append(f"{context}: token {token!r} is not active")
            else:
                active_holds.pop(token, None)
        elif kind == "barrier":
            required = step.get("require-holds", [])
            if not isinstance(required, list):
                errors.append(f"{context}: require-holds must be a list when present")
            else:
                for token in required:
                    if not isinstance(token, str) or not token.strip():
                        errors.append(f"{context}: require-holds entries must be non-empty strings")
                    elif token not in active_holds:
                        errors.append(f"{context}: required hold token {token!r} is not active")

    for token, (index, tab) in sorted(active_holds.items()):
        errors.append(f"step {index}: hold token {token!r} on tab {tab} is never released")
    return errors


def _validate_recipe_payload(recipe_name: str) -> int:
    try:
        from scripts.watchdog.recipe_store import load_recipe
    except Exception as exc:
        console.print(f"  [red]✗[/red]  Recipe store could not be loaded: {exc}. Check that scripts/watchdog/recipe_store.py exists.")
        return 1
    try:
        recipe = load_recipe(recipe_name)
    except Exception as exc:
        console.print(f"  [red]✗[/red]  Recipe '{recipe_name}' could not be loaded: {exc}. Verify the recipe file exists and is valid JSON.")
        return 1
    if not isinstance(recipe, dict):
        console.print("  [red]✗[/red]  Recipe JSON must be an object (dict), not a list or primitive. Check the recipe file structure.")
        return 1
    errors = _validate_recipe_structure(recipe)
    if errors:
        console.print(f"  [red]✗[/red]  Recipe {recipe_name} failed validation:")
        for error in errors[:12]:
            console.print(f"    - {error}")
        if len(errors) > 12:
            console.print(f"    - ... {len(errors) - 12} more")
        console.print("  Fix the recipe file and re-validate with [v] Validate Recipe.")
        return 1
    steps = recipe["steps"]
    console.print(f"  [green]✓[/green]  Recipe {recipe_name} has {len(steps)} step(s).")
    return 0


def _movement_gate_state(summary: dict[str, object]) -> str:
    false_gates = summary.get("false_gates") if isinstance(summary.get("false_gates"), list) else []
    null_gates = summary.get("null_gates") if isinstance(summary.get("null_gates"), list) else []
    if "recipe_movement_coverage_ok" in false_gates:
        return "FAIL"
    if "recipe_movement_coverage_ok" in null_gates:
        return "null"
    return "ok"


def _print_recipe_repeat_summary(recipe_name: str, *, previous_run_id: str | None = None) -> int:
    latest = _latest_run_id_shared(RUNS_ROOT)
    if not latest:
        console.print("  [yellow]⚠[/yellow]  Recipe run summary unavailable: no watchdog runs are recorded on this machine.")
        return 1
    summary = _read_run_summary(latest)
    if not summary:
        console.print(f"  [yellow]⚠[/yellow]  Recipe run summary unavailable: {latest} has no readable summary.")
        return 1
    if previous_run_id and latest == previous_run_id:
        console.print(f"  [yellow]⚠[/yellow]  Latest run is still {latest}; no newer recipe summary was detected.")
    controller_recipe = str(summary.get("controller_recipe") or "").strip()
    controller_result = summary.get("controller_recipe_result") if isinstance(summary.get("controller_recipe_result"), dict) else {}
    controller_summary = controller_result.get("summary") if isinstance(controller_result.get("summary"), dict) else {}
    result_recipe = str(controller_summary.get("recipe") or "").strip()
    if controller_recipe and controller_recipe != recipe_name and result_recipe != recipe_name:
        console.print(f"  [yellow]⚠[/yellow]  Latest run recipe is {controller_recipe}, not {recipe_name}.")
    steps_executed = controller_summary.get("steps_executed", "?")
    step_count = controller_summary.get("step_count", "?")
    recipe_ok = bool(controller_result.get("ok"))
    movement = summary.get("movement_coverage") if isinstance(summary.get("movement_coverage"), dict) else {}
    console.print("  Recipe repeat summary:")
    console.print(f"    run: {latest}")
    console.print(f"    controller_recipe_result: {'ok' if recipe_ok else 'fail'}")
    console.print(f"    steps_executed: {steps_executed}/{step_count}")
    console.print(f"    recipe_failed: {bool(summary.get('recipe_failed'))}")
    if movement:
        console.print(
            "    movement: "
            f"tab1={movement.get('tab1_distinct_positions', 0)} "
            f"tab2={movement.get('tab2_distinct_positions', 0)} "
            f"gate={_movement_gate_state(summary)}"
        )
    else:
        console.print("    movement: unavailable")
    if "recipe_movement_coverage_ok" in (summary.get("false_gates") or []):
        console.print("    gameplay_effect: input executed but movement/effect proof failed")
    elif "recipe_movement_coverage_ok" in (summary.get("null_gates") or []):
        console.print("    gameplay_effect: input executed but movement/effect proof is inconclusive")
    else:
        console.print("    gameplay_effect: see canonical gates/analyzer output for final proof")
    return 0


def _server_snapshot_selector_args(args: argparse.Namespace) -> list[str] | None:
    selectors = [
        ("--at", getattr(args, "at", None)),
        ("--tick", getattr(args, "tick", None)),
        ("--entity", getattr(args, "entity", None)),
    ]
    chosen = [(flag, value) for flag, value in selectors if value not in (None, "")]
    if len(chosen) != 1:
        console.print("  [red]✗[/red]  server-snapshot requires exactly one of --at, --tick, or --entity.")
        console.print("  --at <seconds>    wall-clock time inside the recording (e.g. --at 5.0)")
        console.print("  --tick <n>        server tick number (e.g. --tick 120)")
        console.print("  --entity <tab1>   entity filter (e.g. --entity tab1)")
        return None
    flag, value = chosen[0]
    if flag == "--at":
        try:
            float(value)
        except (TypeError, ValueError):
            console.print(f"  [red]✗[/red]  --at expects a float (seconds), got: {value!r}")
            console.print("  Example: --at 5.0  (wall-clock seconds inside the recording)")
            console.print("  To find a run by ID, use: --action watchdog-dashboard-run-show --run-id <id>")
            return None
    out = [flag, str(value)]
    tab = getattr(args, "tab", None)
    if tab in {"1", "2"}:
        tab = f"tab{tab}"
    if tab in {"tab1", "tab2"}:
        out.extend(["--tab", tab])
    return out


def _run_analyze_for_run(action_label: str, subcommand: str, run_id: str | None) -> int:
    selected = run_id or _latest_run_id_shared(RUNS_ROOT)
    if not selected:
        console.print("  [red]✗[/red]  No watchdog run found. Execute a test run first (Run Ops → Full Run).")
        return 1
    return _run_command([_repo_python(), "scripts/analyze_runs.py", subcommand, selected], label=action_label, cwd=REPO_ROOT)






def _show_vps_liveness_and_context(env: dict[str, str], *, pause_on_success: bool = False) -> int:
    """Enhanced VPS operations display: game-liveness tiers for both slots (FL-2792).

    Shows real game-page liveness (not just nginx green) alongside the
    traditional SSH/WebSocket/manifest probes, plus the latest run context
    with next_action and operator_help surfacing.
    """
    candidate = _load_slot_config(env, "candidate").as_dict()
    current = _load_slot_config(env, "current").as_dict()
    selected_run_id = _latest_run_id_shared(RUNS_ROOT)
    summary = _read_run_summary(selected_run_id) if selected_run_id else None
    artifacts = _watchdog_artifact_status(summary)

    console.print(f"  Server layout: {_topology_value(env)}")
    console.print("  " + _target_context_line(env))
    console.print()

    # -- Slot game-liveness tiers (FL-2792 fix 2: real WS join-byte probe, not just HTTP) --
    for name, cfg in (("candidate (staging)", candidate), ("current (live)", current)):
        console.print(f"  {name}:")
        console.print(f"    Host: {cfg['display_host']}")
        if cfg['ssh_target']:
            console.print(f"    SSH:  {cfg['ssh_target']}")
        for tier_label, tier_status, tier_detail in _slot_game_liveness_tiers(cfg):
            console.print(f"    {tier_label}: {tier_status}  {tier_detail}")
    console.print()

    # -- Latest run summary with next_action surfacing (FL-2792) --
    if selected_run_id:
        console.print(f"  Latest test run: {selected_run_id}")
    else:
        console.print("  Latest test run: none")
    if summary:
        _verdict = summary.get('verdict', '(none)')
        _mode = summary.get('mode', '(none)')
        _target = summary.get('target') or summary.get('slot') or '(none)'
        _controller = summary.get('controller_mode', 'off')
        _recipe = summary.get('controller_recipe') or '(none)'
        _run_label = summary.get('run_label') or summary.get('scenario') or '(none)'
        console.print(f"    Result: {_verdict}  Mode: {_mode}  Target: {_target}")
        if _controller != 'off' or _recipe != '(none)':
            console.print(f"    Controller: {_controller}  Recipe: {_recipe}")
        if _run_label != '(none)':
            console.print(f"    Label: {_run_label}")

        # FL-2792: surface next_action in the context view
        next_action = summary.get("next_action")
        if next_action:
            console.print(f"    Next action: {next_action[:120]}..." if len(str(next_action)) > 120 else f"    Next action: {next_action}")

        if str(_verdict).lower() == "fail":
            console.print("  [dim]Last run FAILED — press [n] for structured next-step guidance[/dim]")
        blocker = summary.get("prelaunch_blocker")
        if isinstance(blocker, dict) and blocker:
            _stage = blocker.get('stage') or summary.get('failed_stage') or '-'
            _reason = blocker.get('reason') or blocker.get('kind') or summary.get('error_kind') or '-'
            console.print(f"    Blocked at: {_stage}  Reason: {_reason}")
    console.print()

    # -- Artifact & source state --
    console.print(f"  Source commit: {artifacts.get('source_ref') or '-'}")
    console.print(f"  Git HEAD:     {artifacts.get('git_head') or '-'}")
    _restorable = artifacts.get('restoreable_by_commit')
    console.print(f"  Can restore:  {'yes' if _restorable else 'no' if _restorable is not None else '-'}")
    _art_summary = artifacts.get('summary', '-')
    _art_raw = artifacts.get('raw', '-')
    _art_archive = artifacts.get('archive', '-')
    _art_manifest = artifacts.get('slot_manifest', '-')
    console.print(f"  Artifacts:    summary={_art_summary}  raw={_art_raw}  archive={_art_archive}  manifest={_art_manifest}")
    for line in _bundle_parity_lines_via_seam():
        console.print(line)

    if pause_on_success:
        _pause("  Press Enter to continue.")
    return 0


def _show_watchdog_context(run_id: str | None = None, *, pause_on_success: bool = False) -> int:
    """Original _show_watchdog_context, kept for _menu_vps_header compatibility.

    Falls through to _show_vps_liveness_and_context which is the enhanced version (FL-2792).
    """
    env = _senv.load()
    return _show_vps_liveness_and_context(env, pause_on_success=pause_on_success)


def _show_context_next_action_hint() -> None:
    """Show a one-line next-action hint from the latest run, if available (FL-2792)."""
    selected_run_id = _latest_run_id_shared(RUNS_ROOT)
    if not selected_run_id:
        return
    summary = _read_run_summary(selected_run_id)
    if not summary:
        return
    next_action = summary.get("next_action")
    if next_action and str(next_action).strip():
        na_text = str(next_action).strip()[:160]
        if len(str(next_action)) > 160:
            na_text += "..."
        console.print(f"  [dim]Next action: {na_text}  (press [n] for details)[/dim]")


def _show_mobile_status(env: dict[str, str] | None = None, *, pause_on_success: bool = False) -> int:
    local_env = _seed_playwright_defaults(dict(env or _senv.load()))
    candidate = _load_slot_config(local_env, "candidate").as_dict()
    node_path = shutil.which("node")
    with _loading("Checking Playwright browsers"):
        browser_ready, browser_detail = _playwright_browser_install_state()
    viewport = local_env.get("PLAYWRIGHT_VIEWPORT", "")
    duration = local_env.get("PLAYWRIGHT_DURATION", "")
    browser = local_env.get("PLAYWRIGHT_BROWSER_ENGINE", "")
    device = local_env.get("PLAYWRIGHT_DEVICE", PLAYWRIGHT_DEFAULTS["PLAYWRIGHT_DEVICE"])
    console.print(f"  node: {node_path or '(not found)'}")
    console.print(f"  viewport: {viewport}")
    console.print(f"  duration: {duration}s")
    console.print(f"  browser: {browser}")
    console.print(f"  device: {device}")
    console.print(f"  browsers: {'ready' if browser_ready else 'not ready'} ({browser_detail})")
    console.print(f"  target URL: {candidate['base_url'] or '-'}")
    console.print(f"  WS server: {candidate['ws_server'] or '-'}")
    verdict = "READY"
    reason = ""
    if not node_path:
        verdict = "NOT READY"
        reason = "Node.js not found"
    elif not browser_ready:
        verdict = "NOT READY"
        reason = browser_detail
    elif not candidate["base_url"] or not candidate["ws_server"]:
        verdict = "NOT READY"
        reason = "candidate test target not configured"
    elif not viewport or not duration or not browser or not device:
        verdict = "NOT READY"
        reason = "mobile config incomplete"
    console.print(f"  verdict: {verdict}{f': {reason}' if reason else ''}")
    if pause_on_success:
        _pause("  Press Enter to continue.")
    return 0


def _show_trust_audit_result(*, pause_on_success: bool = False) -> int:
    latest_json = TRUST_AUDIT_DIR / "latest.json"
    latest_md = TRUST_AUDIT_DIR / "latest.md"
    if latest_json.exists():
        try:
            data = json.loads(latest_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            console.print(f"  [red]✗[/red]  Invalid trust audit artifact: {exc}")
            return 1
        console.print(f"  path: {latest_json}")
        console.print(f"  overall: {data.get('overall_verdict', '-')}")
        console.print(f"  trusted: {data.get('gameplay_fail_interpretability', '-')}")
        console.print(f"  ts: {data.get('ts_utc', '-')}")
    elif latest_md.exists():
        console.print(f"  path: {latest_md}")
        console.print(latest_md.read_text(encoding="utf-8"))
    else:
        console.print("  [yellow]⚠[/yellow]  No trust audit result found in .run/watchdog_trust_audit/.")
        return 1
    if pause_on_success:
        _pause("  Press Enter to continue.")
    return 0


def _print_legacy_trust_audit_warning() -> None:
    console.print(
        "  [yellow]⚠[/yellow]  Legacy trust audit is non-authoritative (FL-1149); "
        "it is not an R1-R9 proof gate."
    )


def _recipe_help(*, pause_on_success: bool = False) -> int:
    with _capturing_console() as _cap_buf:
        console.print()
        console.rule("[bold]Recipe Workflow[/bold]")
        console.print()
        console.print("  [bold]What a recipe is[/bold]")
        console.print("  A recipe is a JSON punch card stored in scripts/watchdog_recipes/.")
        console.print("  It records scripted human-like controller input (key presses, holds,")
        console.print("  waits) that the watchdog replays after game launch, enabling repeatable")
        console.print("  gameplay proofs without a human operator at the keyboard.")
        console.print()
        console.print("  [bold]Quick path[/bold]  (recommended)")
        console.print("    Run a manual/controller watchdog proof and pass:")
        console.print("    [cyan]--followup-repeat-with-derived-recipe[/cyan]")
        console.print("    The system automatically derives and runs a repeat recipe from")
        console.print("    your input recording. No separate capture step needed.")
        console.print()
        console.print("  [bold]Explicit path[/bold]  (step by step)")
        console.print("    1. Run a manual or controller watchdog proof.")
        console.print("    2. [r] Make recipe from run — captures your input as a stored recipe.")
        console.print("    3. [p] Repeat recipe — replays the stored recipe against the live slot.")
        console.print()
        console.print("  [bold]Recipe step kinds[/bold]")
        console.print("    [cyan]validate-attach[/cyan]   confirm both browser tabs are attached to the game")
        console.print("    [cyan]sleep[/cyan]             wait N milliseconds")
        console.print("    [cyan]send-key[/cyan]          press a single key")
        console.print("    [cyan]send-sequence[/cyan]     press a sequence of keys")
        console.print("    [cyan]hold-key-begin[/cyan]    hold a key down (pair with hold-key-end)")
        console.print("    [cyan]hold-key-end[/cyan]      release a held key")
        console.print("    [cyan]barrier[/cyan]           wait for a named game-state condition")
        console.print()
        console.print("  [bold]Useful commands[/bold]")
        console.print("    List stored recipes:  [cyan]scripts/watchdog/recipe_store.py list[/cyan]")
        console.print("    Show recipe contents: [cyan]scripts/watchdog/recipe_store.py show NAME[/cyan]")
        console.print("    Capture from run:     [cyan]scripts/watchdog/recipe_store.py capture-from-run RUN_ID --recipe-name NAME[/cyan]")
        console.print()
    if _cap_buf is not None:
        _ScrollView(_renderer).show_lines(_cap_buf.getvalue().rstrip("\n").split("\n"))
    elif pause_on_success:
        _pause("  Press Enter to continue.")
    return 0


def _recipe_export_command(recipe_name: str) -> list[str]:
    return [_repo_python(), "scripts/watchdog/recipe_store.py", "show", recipe_name]


def _print_option_tree_json() -> int:
    _write_stdout_text(json.dumps(_option_tree.option_tree(), indent=2))
    return 0


def _show_health_json() -> int:
    _write_stdout_text(json.dumps(_health.full_health_check_json(), indent=2))
    return 0


def _list_valid_actions(*, as_json: bool = False) -> int:
    """List every action registered in the agent bridge.

    Previously scraped _execute_action source with regex; now uses the
    canonical ActionRegistry populated by launcher_agent_bridge.py.
    """
    all_actions = _action_registry.names()
    if as_json:
        _write_stdout_text(json.dumps(all_actions))
    else:
        for a in all_actions:
            _write_stdout_text(a)
    return 0


def _show_watchdog_context_json(run_id: str | None = None) -> int:
    env = _senv.load()
    candidate = _load_slot_config(env, "candidate").as_dict()
    current = _load_slot_config(env, "current").as_dict()
    selected_run_id = run_id or _latest_run_id_shared(RUNS_ROOT)
    summary = _read_run_summary(selected_run_id) if selected_run_id else None
    artifacts = _watchdog_artifact_status(summary)
    slots = {}
    for name, cfg in (("candidate", candidate), ("current", current)):
        slots[name] = {
            "host": cfg["display_host"],
            "ssh": cfg["ssh_target"] or None,
            "base": cfg["base_url"] or None,
            "ws": cfg["ws_server"] or None,
            "liveness": [
                {"label": lbl, "status": st, "detail": det}
                for lbl, st, det in _slot_game_liveness_tiers(cfg)
            ],
        }
    run_summary_out = None
    if summary:
        blocker = summary.get("prelaunch_blocker")
        blocker_out = None
        if isinstance(blocker, dict) and blocker:
            blocker_out = {
                "stage": blocker.get("stage") or summary.get("failed_stage") or None,
                "reason": blocker.get("reason") or blocker.get("kind") or summary.get("error_kind") or None,
            }
        run_summary_out = {
            "verdict": summary.get("verdict"),
            "mode": summary.get("mode"),
            "target": summary.get("target") or summary.get("slot") or None,
            "controller": summary.get("controller_mode", "off"),
            "recipe": summary.get("controller_recipe") or None,
            "run_label": summary.get("run_label") or summary.get("scenario") or None,
            "blocked": blocker_out,
            "next_action": summary.get("next_action"),  # FL-2792: surface next_action in JSON too
        }
    data = {
        "topology": _topology_value(env),
        "target_context": _target_context_line(env),
        **slots,
        "latest_run": selected_run_id,
        "run_summary": run_summary_out,
        "artifacts": artifacts,
    }
    _write_stdout_text(json.dumps(data, indent=2))
    return 0


def _show_mobile_status_json(env: dict[str, str] | None = None) -> int:
    local_env = _seed_playwright_defaults(dict(env or _senv.load()))
    candidate = _load_slot_config(local_env, "candidate").as_dict()
    node_path = shutil.which("node")
    browser_ready, browser_detail = _playwright_browser_install_state()
    viewport = local_env.get("PLAYWRIGHT_VIEWPORT", "")
    duration = local_env.get("PLAYWRIGHT_DURATION", "")
    browser = local_env.get("PLAYWRIGHT_BROWSER_ENGINE", "")
    device = local_env.get("PLAYWRIGHT_DEVICE", PLAYWRIGHT_DEFAULTS["PLAYWRIGHT_DEVICE"])
    verdict = "READY"
    reason = ""
    if not node_path:
        verdict = "NOT READY"
        reason = "Node.js not found"
    elif not browser_ready:
        verdict = "NOT READY"
        reason = browser_detail
    elif not candidate["base_url"] or not candidate["ws_server"]:
        verdict = "NOT READY"
        reason = "candidate test target not configured"
    elif not viewport or not duration or not browser or not device:
        verdict = "NOT READY"
        reason = "mobile config incomplete"
    data = {
        "node": node_path or None,
        "viewport": viewport or None,
        "duration": duration or None,
        "browser": browser or None,
        "device": device or None,
        "browsers_ready": browser_ready,
        "browsers_detail": browser_detail,
        "target_url": candidate["base_url"] or None,
        "ws_server": candidate["ws_server"] or None,
        "verdict": verdict,
        "reason": reason or None,
    }
    _write_stdout_text(json.dumps(data, indent=2))
    return 0


def _run_or_build(
    binary: str,
    make_target: str,
    label: str,
    args: list[str] | None = None,
    *,
    env: dict[str, str] | None = None,
) -> int:
    bin_path = _ensure_built_binary(binary, make_target, label)
    if bin_path is None:
        return 0
    if not bin_path.is_file():
        return 1
    cmd = [str(bin_path)]
    if args:
        cmd.extend(args)
    _print_copyable_command(cmd, env=env)
    # Native binary needs full screen — exit altscreen while it runs.
    # Stop buffering so post-run console.print goes to the real console.
    if _renderer.active:
        if _io_mgr.submenu_buffering:
            _flush_and_render(f"  Launching {label}...")
            _stop_submenu_buffer()
        with _renderer.paused():
            result = subprocess.run(cmd, env=env)
    else:
        result = subprocess.run(cmd, env=env)
    return result.returncode


def _ensure_built_binary(binary: str, make_target: str, label: str) -> Path | None:
    bin_path = RUN_DIR / binary
    if not bin_path.is_file():
        build_cmd = ["make", "-C", str(REPO_ROOT), make_target]
        if not _can_prompt():
            console.print(f"  [red]✗[/red]  [bold]{label}[/bold] not built (.run/{binary} missing).")
            _print_copyable_command(build_cmd)
            return bin_path
        console.print(f"  [yellow]⚠[/yellow]  [bold]{label}[/bold] not built (.run/{binary} missing).")
        answer = _prompt_choice("  Build now? [Y/n]: ").lower()
        if answer not in ("", "y"):
            return None
        _print_copyable_command(build_cmd)
        # Native build requires exiting altscreen — SDL/make write to stdout
        if _renderer.active:
            if _io_mgr.submenu_buffering:
                _stop_submenu_buffer()
            with _renderer.paused():
                result = subprocess.run(build_cmd)
        else:
            result = subprocess.run(build_cmd)
        if result.returncode != 0:
            console.print(f"  [red]✗[/red]  Build failed. Run [bold]make {make_target}[/bold] for details.")
            _pause("  Press Enter to return to menu.")
            return bin_path
        console.print(f"  ✓  {label} built.")
    return bin_path


def _argv_has_max_players_one(argv: list[str]) -> bool:
    for idx, arg in enumerate(argv):
        if arg == "--max-players" and idx + 1 < len(argv) and argv[idx + 1] == "1":
            return True
        if arg == "--max-players=1":
            return True
    return False


def _repo_local_server_pids(*, max_players_one_only: bool = False) -> list[int]:
    server_path = (RUN_DIR / "server").resolve()
    try:
        result = subprocess.run(
            ["ps", "ax", "-o", "pid=", "-o", "command="],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []

    pids: list[int] = []
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        if pid == os.getpid():
            continue
        command = parts[1]
        try:
            argv = shlex.split(command)
        except ValueError:
            argv = command.split()
        if not argv:
            continue
        try:
            argv0 = Path(argv[0]).resolve()
        except OSError:
            continue
        if argv0 != server_path:
            continue
        if max_players_one_only and not _argv_has_max_players_one(argv):
            continue
        if not max_players_one_only or _argv_has_max_players_one(argv):
            pids.append(pid)
    return pids


def _repo_single_player_server_pids() -> list[int]:
    return _repo_local_server_pids(max_players_one_only=True)


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _stop_repo_single_player_servers() -> list[int]:
    pids = _repo_single_player_server_pids()
    return _stop_repo_local_server_pids(pids, label="prior repo-local single-player", suffix=" before launch")


def _stop_repo_local_server_pids(pids: list[int], *, label: str, suffix: str = "") -> list[int]:
    if not pids:
        return []

    stopped: list[int] = []
    for pid in pids:
        try:
            pgid = os.getpgid(pid)
            os.killpg(pgid, signal.SIGTERM)
            stopped.append(pid)
        except ProcessLookupError:
            continue
        except PermissionError:
            console.print(f"  [yellow]⚠[/yellow]  cannot stop {label} server pid {pid}: permission denied")
        except OSError:
            # getpgid failed (e.g. pid already gone) — fall back to direct kill
            try:
                os.kill(pid, signal.SIGTERM)
                stopped.append(pid)
            except (ProcessLookupError, PermissionError):
                pass

    for pid in stopped:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and _pid_exists(pid):
            time.sleep(0.05)

    for pid in stopped:
        if not _pid_exists(pid):
            continue
        try:
            pgid = os.getpgid(pid)
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            continue
        except PermissionError:
            console.print(f"  [yellow]⚠[/yellow]  cannot kill {label} server pid {pid}: permission denied")
        except OSError:
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    console.print(
        f"  [dim]Stopped {len(stopped)} {label} server"
        f"{'' if len(stopped) == 1 else 's'}{suffix}.[/dim]"
    )
    return stopped


def _stop_repo_local_servers() -> int:
    state = _read_json_file(LOCAL_SERVER_STATE_PATH)
    if not state:
        console.print("  [dim]No launcher-owned local server is recorded.[/dim]")
        return 0
    try:
        pid = int(state.get("pid", 0))
    except (TypeError, ValueError):
        pid = 0
    if pid <= 0:
        console.print("  [yellow]⚠[/yellow]  Local server state has no valid pid; clearing it.")
        LOCAL_SERVER_STATE_PATH.unlink(missing_ok=True)
        return 0
    if not _pid_exists(pid):
        console.print(f"  [yellow]⚠[/yellow]  Launcher-owned local server pid {pid} is stale; clearing it.")
        LOCAL_SERVER_STATE_PATH.unlink(missing_ok=True)
        return 0
    _stop_repo_local_server_pids([pid], label="launcher-owned local")
    LOCAL_SERVER_STATE_PATH.unlink(missing_ok=True)
    return 0


def _load_a3d_format():
    mod_path = REPO_ROOT / "addons" / "io_asciicker" / "scene" / "a3d_format.py"
    spec = importlib.util.spec_from_file_location("launcher_a3d_format", mod_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load A3D format from {mod_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_a3d_import_core():
    mod_path = REPO_ROOT / "addons" / "io_asciicker" / "scene" / "a3d_import_core.py"
    spec = importlib.util.spec_from_file_location("launcher_a3d_import_core", mod_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load A3D import core from {mod_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _terrain_metadata_spawn_hint(map_path: Path) -> tuple[float, float] | None:
    meta_path = map_path.with_name("terrain_metadata.json")
    if not meta_path.is_file():
        return None
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    bounds = data.get("content_bounds") or data.get("terrain_bounds")
    if not isinstance(bounds, dict):
        return None
    try:
        min_x = float(bounds["min_x"])
        min_y = float(bounds["min_y"])
        max_x = float(bounds["max_x"])
        max_y = float(bounds["max_y"])
    except (KeyError, TypeError, ValueError):
        return None
    return ((min_x + max_x) / 2.0, (min_y + max_y) / 2.0)


def _sample_patch_height(patch, fmt, world_x: float, world_y: float) -> float:
    patch_world = float(fmt.VISUAL_CELLS)
    vertex_step = patch_world / float(fmt.HEIGHT_CELLS)
    local_x = max(0.0, min(patch_world, world_x - patch.x * patch_world))
    local_y = max(0.0, min(patch_world, world_y - patch.y * patch_world))
    fx = local_x / vertex_step
    fy = local_y / vertex_step
    x0 = min(fmt.HEIGHT_CELLS - 1, max(0, int(math.floor(fx))))
    y0 = min(fmt.HEIGHT_CELLS - 1, max(0, int(math.floor(fy))))
    x1 = min(fmt.HEIGHT_CELLS, x0 + 1)
    y1 = min(fmt.HEIGHT_CELLS, y0 + 1)
    tx = max(0.0, min(1.0, fx - x0))
    ty = max(0.0, min(1.0, fy - y0))
    h00 = float(patch.height[y0][x0])
    h10 = float(patch.height[y0][x1])
    h01 = float(patch.height[y1][x0])
    h11 = float(patch.height[y1][x1])
    hx0 = h00 + (h10 - h00) * tx
    hx1 = h01 + (h11 - h01) * tx
    return hx0 + (hx1 - hx0) * ty


def _embedded_player_start_for_map(map_path: Path) -> tuple[float, float, float] | None:
    try:
        core = _load_a3d_import_core()
        player_start = core.read_player_start(str(map_path))
    except Exception:
        return None
    if player_start is None:
        return None
    return (float(player_start.pos[0]), float(player_start.pos[1]), float(player_start.pos[2]))


def _inspect_map_spawn_point(map_path: Path) -> tuple[float, float, float] | None:
    """Return a map-owned player-start, or synthesize one for legacy maps.

    FL-2540 owner move: prefer the player-start embedded in the selected A3D.
    Only synthesize from terrain metadata / patch bounds for old maps that do
    not yet carry the map-owned player-start record.
    """
    embedded = _embedded_player_start_for_map(map_path)
    if embedded is not None:
        return embedded

    try:
        fmt = _load_a3d_format()
    except Exception:
        return None

    target = _terrain_metadata_spawn_hint(map_path)
    patch_world = float(fmt.VISUAL_CELLS)
    min_patch_x = min_patch_y = None
    max_patch_x = max_patch_y = None

    with open(map_path, "rb") as fh:
        hdr = fmt.A3DHeader.from_file(fh)
        for _ in range(hdr.num_patches):
            patch = fmt.A3DPatch.from_file(fh)
            min_patch_x = patch.x if min_patch_x is None else min(min_patch_x, patch.x)
            min_patch_y = patch.y if min_patch_y is None else min(min_patch_y, patch.y)
            max_patch_x = patch.x if max_patch_x is None else max(max_patch_x, patch.x)
            max_patch_y = patch.y if max_patch_y is None else max(max_patch_y, patch.y)

            if target is not None:
                tx, ty = target
                px0 = patch.x * patch_world
                py0 = patch.y * patch_world
                if px0 <= tx <= px0 + patch_world and py0 <= ty <= py0 + patch_world:
                    return (tx, ty, _sample_patch_height(patch, fmt, tx, ty))

    if min_patch_x is None or min_patch_y is None or max_patch_x is None or max_patch_y is None:
        return None

    if target is None:
        target = (
            ((min_patch_x + max_patch_x + 1) * patch_world) / 2.0,
            ((min_patch_y + max_patch_y + 1) * patch_world) / 2.0,
        )

    tx, ty = target
    nearest = None
    with open(map_path, "rb") as fh:
        hdr = fmt.A3DHeader.from_file(fh)
        for _ in range(hdr.num_patches):
            patch = fmt.A3DPatch.from_file(fh)
            cx = patch.x * patch_world + patch_world / 2.0
            cy = patch.y * patch_world + patch_world / 2.0
            dist2 = (cx - tx) ** 2 + (cy - ty) ** 2
            if nearest is None or dist2 < nearest[0]:
                nearest = (dist2, patch, cx, cy)

    if nearest is None:
        return None
    _dist2, patch, cx, cy = nearest
    return (cx, cy, _sample_patch_height(patch, fmt, cx, cy))


def _single_player_spawn_env_for_map(map_path: Path) -> dict[str, str] | None:
    embedded = _embedded_player_start_for_map(map_path)
    if embedded is not None:
        x, y, z = embedded
        return {
            "ASCIICKER_SPAWN_X": f"{x:.3f}",
            "ASCIICKER_SPAWN_Y": f"{y:.3f}",
            "ASCIICKER_SPAWN_Z": f"{z:.3f}",
        }
    point = _inspect_map_spawn_point(map_path)
    if point is None:
        return None
    x, y, z = point
    return {
        "ASCIICKER_SPAWN_X": f"{x:.3f}",
        "ASCIICKER_SPAWN_Y": f"{y:.3f}",
        "ASCIICKER_SPAWN_Z": f"{z + 200.0:.3f}",
    }


def _merge_map_mesh_root_env(map_path: Path, env: dict[str, str] | None = None) -> dict[str, str] | None:
    """Pair native runtime launches with the selected run-local mesh root.

    FL-2534 / FL-2553: traditional OSM outputs embed mesh instances by name.
    If the native launcher opens the right `.a3d` but lets world.cpp fall back
    to root `assets/meshes/*.akm`, the runtime silently swaps in stale tiny
    legacy meshes and reintroduces the same visible-size regression.
    """
    base_env = dict(env) if env else None
    return _asciiid_app._merge_mesh_root_env(["--map", str(map_path)], REPO_ROOT, base_env)


def _local_host_preferences() -> dict[str, object]:
    return _read_json_file(LOCAL_HOST_PREFS_PATH) or {}


def _local_host_max_players() -> int:
    prefs = _local_host_preferences()
    try:
        value = int(prefs.get("max_players", DEFAULT_LOCAL_MAX_PLAYERS))
    except (TypeError, ValueError):
        value = DEFAULT_LOCAL_MAX_PLAYERS
    return value if value > 0 else DEFAULT_LOCAL_MAX_PLAYERS


def _set_local_host_max_players(
    args: argparse.Namespace | None = None,
    *,
    pause_on_success: bool = False,
) -> int:
    selected = getattr(args, "max_players", None) if args is not None else None
    if selected is None:
        if not _can_prompt():
            console.print("  [red]✗[/red]  --max-players is required in non-interactive mode. Usage: python3 scripts/launcher.py --action set-max-players --max-players 4")
            return EXIT_MISSING_ARG
        chosen = _choose_max_players()
        if chosen is None:
            console.print("  [dim]Cancelled.[/dim]")
            return 0
        max_players = chosen
    _write_json_file(
        LOCAL_HOST_PREFS_PATH,
        {
            "max_players": max_players,
            "written_by": "scripts/launcher.py",
            "mtime": int(time.time()),
        },
    )
    console.print(f"  [green]✓[/green]  Host Local max players saved: {max_players}")
    if pause_on_success:
        _pause("  Press Enter to continue.")
    return 0


def _local_host_map(raw_path: str | None = None) -> Path | None:
    if raw_path:
        return _selected_or_arg_map(raw_path)
    if _SELECTED_MAP_PATH is not None:
        return _selected_or_arg_map(None)
    return _read_map_selection("single-player") or DEFAULT_MAP


def _host_local_play_with_friends(args: argparse.Namespace | None = None, *, pause_on_success: bool = False) -> int:
    server = RUN_DIR / "server"
    if not server.is_file():
        build_cmd = ["make", "-C", str(REPO_ROOT), "server"]
        rc = _run_command(build_cmd, label="local server build", cwd=REPO_ROOT)
        if rc != 0:
            return rc
    map_path = _local_host_map(getattr(args, "map_path", None) if args is not None else None)
    if map_path is None:
        return 1
    try:
        port = int(getattr(args, "port", None) or DEFAULT_LOCAL_WEB_PORT)
        max_players = int(getattr(args, "max_players", None) or _local_host_max_players())
    except (TypeError, ValueError):
        console.print("  [red]✗[/red]  --port and --max-players must be integers.")
        return 1
    if port <= 0 or max_players <= 0:
        console.print("  [red]✗[/red]  --port and --max-players must be positive.")
        return 1

    _stop_repo_local_servers()
    log_path = RUN_DIR / "launcher-local-server.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [str(server), "--port", str(port), "--max-players", str(max_players), "--map", _repo_relative(map_path)]
    launch_env = _merge_map_mesh_root_env(map_path)
    if launch_env and launch_env.get("ASCIICKER_ACTIVE_MESH_ROOT"):
        console.print(
            "  [dim]Using run-local mesh root "
            f"({launch_env['ASCIICKER_ACTIVE_MESH_ROOT']}) for local host.[/dim]"
        )
    _print_copyable_command(cmd, cwd=REPO_ROOT, env=launch_env)
    try:
        log_fh = log_path.open("ab")
        proc = subprocess.Popen(
            cmd,
            cwd=str(REPO_ROOT),
            env=launch_env,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        log_fh.close()
    except OSError as exc:
        console.print(f"  [red]✗[/red]  failed to start local server: {exc}")
        return 1

    import atexit as _atexit

    def _cleanup_server_proc() -> None:
        if proc.poll() is None:
            try:
                import os as _os
                _os.killpg(_os.getpgid(proc.pid), signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass

    _atexit.register(_cleanup_server_proc)

    time.sleep(0.25)
    if proc.poll() is not None:
        console.print(f"  [red]✗[/red]  local server exited immediately; see {_repo_relative(log_path)}")
        return proc.returncode or 1

    join_url = f"http://127.0.0.1:{port}/"
    state = {
        "pid": proc.pid,
        "command": cmd,
        "join_url": join_url,
        "map": _repo_relative(map_path),
        "log": _repo_relative(log_path),
        "written_by": "scripts/launcher.py",
        "mtime": int(time.time()),
    }
    _write_json_file(LOCAL_SERVER_STATE_PATH, state)
    urls = _local_server_urls(state)
    console.print(f"  [green]✓[/green]  Local server pid {proc.pid}")
    console.print("  Join from this machine:")
    console.print(f"    {urls[0]}", markup=False, highlight=False)
    if len(urls) > 1:
        console.print("  Join from another LAN terminal/device:")
        for url in urls[1:]:
            console.print(f"    {url}", markup=False, highlight=False)
    else:
        console.print("  [yellow]⚠[/yellow]  No non-loopback LAN address was detected.")
    console.print(f"  log: {_repo_relative(log_path)}")
    if pause_on_success:
        _pause("  Press Enter to continue.")
    return 0


def _run_single_player() -> int:
    _stop_repo_single_player_servers()
    map_path = _single_player_map_for_launch()
    env = _merge_map_mesh_root_env(map_path, _single_player_spawn_env_for_map(map_path))
    if env and env.get("ASCIICKER_SPAWN_X"):
        console.print(
            "  [dim]Using map-local spawn "
            f"({env['ASCIICKER_SPAWN_X']}, {env['ASCIICKER_SPAWN_Y']}, {env['ASCIICKER_SPAWN_Z']}) "
            "for native single-player.[/dim]"
        )
    else:
        console.print("  [yellow]⚠[/yellow]  no map-local spawn hint found; native single-player will use the legacy default spawn.")
    if env and env.get("ASCIICKER_ACTIVE_MESH_ROOT"):
        console.print(
            "  [dim]Using run-local mesh root "
            f"({env['ASCIICKER_ACTIVE_MESH_ROOT']}) for native single-player.[/dim]"
        )
    return _run_or_build("game", "game", "Game", [str(map_path)], env=env)


def _run_asciiid_editor(
    label: str,
    *,
    map_path: str | None = None,
    sprite_path: str | None = None,
    sprite_browser: bool = False,
    viewer: bool = False,
) -> int:
    bin_path = _ensure_built_binary("asciiid", "editor", label)
    if bin_path is None:
        return 0
    if not bin_path.is_file():
        return 1
    args: list[str] = []
    if viewer:
        args.append("--viewer")
    if map_path:
        args.extend(["--map", map_path])
    if sprite_browser:
        args.append("--sprite-browser")
    if sprite_path:
        args.extend(["--sprite", sprite_path])
    cmd = [str(bin_path), *args]
    _print_copyable_command(cmd)
    if _renderer.active:
        if _io_mgr.submenu_buffering:
            _flush_and_render(f"  Launching {label}...")
            _stop_submenu_buffer()
        with _renderer.paused():
            result = _asciiid_app.launch_asciiid_gui(args, cwd=REPO_ROOT, binary_path=bin_path)
    else:
        result = _asciiid_app.launch_asciiid_gui(args, cwd=REPO_ROOT, binary_path=bin_path)
    return result.returncode


def _visible_len(text: str) -> int:
    return len(re.sub(r"\[/?[^\]]*\]", "", text))


def _pad_name(name: str, width: int) -> str:
    return name + (" " * max(0, width - _visible_len(name)))


def _branch_notice_text(noun: str, detail: str) -> str:
    detail_key = detail.strip().lower()
    if detail_key == "build":
        return f"{noun} binary missing"
    if detail_key in {"host", "setup", "setup incomplete", "not configured"}:
        return f"{noun} not configured"
    if detail_key in {"down", "ws down"}:
        return f"{noun} unreachable"
    if detail_key:
        return f"{noun} {detail_key}"
    return f"{noun} degraded"


def _maybe_show_branch_notice(noun: str, status: str, detail: str) -> None:
    if _health.menu_badge(status, detail) is None:
        return
    icon = "[red]✗[/red]" if status == "fail" else "[yellow]⚠[/yellow]"
    command = _resolved_shell_command([_repo_python(), "scripts/launcher.py", "--health-json"], cwd=REPO_ROOT)
    console.print(f"  {icon} {_branch_notice_text(noun, detail)}")
    console.print(f"  Run: {command}", markup=False, highlight=False)
    _pause("  Press Enter to continue.")


def _draw_menu(bar: _health.StatusBar) -> None:
    if _fancy_terminal_ui_enabled():
        cols, _ = shutil.get_terminal_size(fallback=(80, 24))
        banner = _build_banner_str(_banner_target_cols(cols))
        if banner:
            sys.stdout.write("\033[H\033[2J" + banner + "\n")
            sys.stdout.flush()
    slot_badges = {
        "1": _health.menu_badge(bar.game, bar.game_detail),
        "2": _health.menu_badge(bar.map_tools, bar.map_detail),
        "3": None,
    }
    body_lines = ["[dim]SCRIPTS LAUNCHER[/dim]", bar.render(), bar.render_front_door(), ""]
    for key, name, desc in MENU_ITEMS:
        padded = _pad_name(name, MENU_NAME_WIDTH)
        badge = escape(f"[{key}]")
        suffix = f" {slot_badges[key]}" if slot_badges.get(key) else ""
        if desc:
            body_lines.append(f"[bold red]{badge}[/bold red] [bold]{padded}[/bold]{suffix} {desc}")
        else:
            body_lines.append(f"[bold red]{badge}[/bold red] [bold]{padded}[/bold]{suffix}")
    body = "\n".join(body_lines)

    console.print()
    if _ui_fancy.enabled():
        console.print(_ui_fancy.menu_panel("Asciicker Launcher", body))
    else:
        console.rule(bar.render(), style="dim")
        console.print("[dim]SCRIPTS LAUNCHER[/dim]")
        console.print(f"[dim]{bar.render_front_door()}[/dim]")
        console.print()
        for key, name, desc in MENU_ITEMS:
            padded = _pad_name(name, MENU_NAME_WIDTH)
            badge = escape(f"[{key}]")
            suffix = f" {slot_badges[key]}" if slot_badges.get(key) else ""
            if desc:
                console.print(f"  [bold red]{badge}[/bold red] [bold]{padded}[/bold]{suffix} {desc}")
            else:
                console.print(f"  [bold red]{badge}[/bold red] [bold]{padded}[/bold]{suffix}")
    console.print()


def _menu_single_player() -> None:
    _run_single_player()


def _menu_game() -> None:
    warned = False
    while True:
        _draw_submenu_header("Game")
        with _loading("Checking health"):
            bar = _health.fast_probes()
        if not warned:
            _maybe_show_branch_notice("game", bar.game, bar.game_detail)
            warned = True
        game_badge = _health.menu_badge(bar.game, bar.game_detail) or ""
        multiplayer_badge = _health.menu_badge(bar.multiplayer, bar.multiplayer_detail) or ""
        # FL-1351: append hint when test-failed is shown so user knows where to look
        if "test failed" in multiplayer_badge:
            multiplayer_badge = multiplayer_badge + "  [dim](see [3] CONFIG for details)[/dim]"
        _menu_line("  [1] SINGLE PLAYER   local play (builds if needed)", suffix_markup=game_badge or None)
        _menu_line("  [2] MULTIPLAYER     host / join / settings", suffix_markup=multiplayer_badge or None)
        _menu_line("  [q] Back")
        choice = _prompt_char("> ")

        if choice == "q":
            return
        if choice == "1":
            _menu_single_player()
            continue
        if choice == "2":
            _menu_multiplayer()
            continue
        console.print(f"  [dim]Unknown key: {choice!r}[/dim]")


def _menu_asciiid() -> None:
    picked = _file_picker(ASSET_DIR / "a3d", filter_glob="*.a3d", title="Select A3D map")
    if picked is None:
        return
    map_path = str(picked)
    map_path_obj, error = _validate_a3d_map_path(map_path)
    if error or map_path_obj is None:
        console.print(f"  [red]x[/red]  {error or 'invalid map path'}")
        _pause("  Press Enter to continue.")
        return
    console.print()
    console.print(f"  [dim]Map:[/dim] {_repo_relative(map_path_obj)}")
    console.print(
        "  [dim]Note: asciiid may print many [SPRITE] info lines. Errors have [ERROR] or [WARN] prefix.[/dim]"
    )
    console.print()
    _run_asciiid_editor("Map Editor", map_path=_repo_relative(map_path_obj))


def _run_map_inspect(map_path: str, extra_flags: list[str] | None = None) -> int:
    args = [_repo_python(), "scripts/inspect_a3d.py", map_path, *(extra_flags or [])]
    return _run_command(args, label="inspect_a3d", cwd=REPO_ROOT)


def _run_map_validate(paths: list[str]) -> int:
    return _run_command([_repo_python(), "scripts/validate_a3d.py", *paths], label="validate_a3d", cwd=REPO_ROOT)


def _run_instance_list(map_path: str) -> int:
    return _run_command([_repo_python(), "docs/agent/cli-anything/a3d_edit.py", "list", map_path], label="instance list", cwd=REPO_ROOT)


def _run_instance_delete(map_path: str, pattern: str) -> int:
    return _run_command(
        [_repo_python(), "docs/agent/cli-anything/a3d_edit.py", "delete", map_path, "--match", pattern],
        label="instance delete",
        cwd=REPO_ROOT,
    )


def _run_new_test_map(out_path: str, grid: str, material: str) -> int:
    # Accept NxN format (e.g. "2x2") as well as bare integer (e.g. "2"); gen_minimal_a3d.py wants bare N
    grid_n = grid.split("x")[0].strip() if "x" in grid.lower() else grid
    return _run_command(
        [_repo_python(), "scripts/gen_minimal_a3d.py", "--out", out_path, "--grid", grid_n, "--material-id", material],
        label="gen_minimal_a3d",
        cwd=REPO_ROOT,
        env=_tool_env(str(REPO_ROOT), str(REPO_ROOT / "addons")),
    )


def _repo_relative(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _repo_relative_or_empty(picked: Path | None) -> str:
    """Return repo-relative string for a picked Path, or '' if None."""
    if not picked:
        return ""
    return _repo_relative(picked)


def _resolve_launcher_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


def _validate_a3d_map_path(raw_path: str) -> tuple[Path | None, str | None]:
    path = _resolve_launcher_path(raw_path)
    if not path.exists():
        return None, f"map not found: {_repo_relative(path)}"
    if not path.is_file():
        return None, f"map path is not a file: {_repo_relative(path)}"
    if path.suffix.lower() != ".a3d":
        return None, f"map path must end in .a3d: {_repo_relative(path)}"
    return path, None


def _set_selected_map(path: Path) -> Path:
    global _SELECTED_MAP_PATH
    _SELECTED_MAP_PATH = path.resolve()
    return _SELECTED_MAP_PATH


def _selected_or_arg_map(raw_path: str | None) -> Path | None:
    if raw_path:
        path, error = _validate_a3d_map_path(raw_path)
        if error:
            console.print(f"  [red]x[/red]  {error}")
            return None
        assert path is not None
        return _set_selected_map(path)
    if _SELECTED_MAP_PATH is not None:
        path, error = _validate_a3d_map_path(str(_SELECTED_MAP_PATH))
        if error:
            console.print(f"  [red]x[/red]  selected map is no longer valid: {error}")
            return None
        assert path is not None
        return path
    console.print("  [red]x[/red]  no selected map; select one in List Maps or pass --map <path>.")
    return None


def _map_selection_payload(target: str, path: Path) -> dict[str, object]:
    resolved = path.resolve()
    return {
        "target": target,
        "map": _repo_relative(resolved),
        "map_abs": str(resolved),
        "written_by": "scripts/launcher.py",
        "contract": "launcher-map-selection-v1",
        "mtime": int(time.time()),
    }


def _write_map_selection(target: str, path: Path) -> Path:
    if target not in MAP_SELECTION_FILES:
        raise ValueError(f"unsupported map selection target: {target}")
    MAP_SELECTION_DIR.mkdir(parents=True, exist_ok=True)
    output = MAP_SELECTION_FILES[target]
    tmp = output.with_name(output.name + ".tmp")
    payload = _map_selection_payload(target, path)
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, output)
    return output


def _read_map_selection(target: str) -> Path | None:
    path = MAP_SELECTION_FILES[target]
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    raw_map = payload.get("map_abs") or payload.get("map")
    if not isinstance(raw_map, str) or not raw_map:
        return None
    selected, error = _validate_a3d_map_path(raw_map)
    if error:
        console.print(f"  [yellow]⚠[/yellow]  ignoring invalid saved {target} map: {error}")
        return None
    return selected


def _single_player_map_for_launch() -> Path:
    return _read_map_selection("single-player") or DEFAULT_MAP


def _prove_asciiid_loads_map(path: Path) -> bool:
    asciiid = RUN_DIR / "asciiid"
    if not asciiid.is_file():
        console.print("  [red]x[/red]  ASCIIID not built (.run/asciiid missing).")
        _print_copyable_command(["make", "-C", str(REPO_ROOT), "editor"])
        return False

    map_arg = _repo_relative(path)
    map_arg_abs = str(path.resolve())
    env = _merge_map_mesh_root_env(path)
    cmd = [str(asciiid), "--headless-batch", "--map", map_arg]
    _print_copyable_command(cmd, env=env)
    try:
        result = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            env=env,
            input="",
            text=True,
            capture_output=True,
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        console.print(f"  [red]x[/red]  ASCIIID load proof timed out for {map_arg}.")
        return False

    stdout = result.stdout or ""
    stderr = result.stderr or ""
    combined_output = f"{stdout}\n{stderr}"
    loaded_markers = (
        f"[EDITOR] Loading map: {map_arg}",
        f"[HEADLESS] Loading map: {map_arg}",
        f"[EDITOR] [FL-3714] Load begin path={map_arg}",
        f"[EDITOR] [FL-3714] Load begin path={map_arg_abs}",
        f"[MCP] Loading map:",
        f"[MCP] Loading map: {map_arg}",
        f"[MCP] Loading map: {map_arg_abs}",
        "[MCP] Map loaded:",
    )
    failed_markers = ("Error: Failed to load map", "Error: Failed to load map for")
    if (
        result.returncode != 0
        or not any(marker in combined_output for marker in loaded_markers)
        or any(marker in combined_output for marker in failed_markers)
    ):
        console.print(f"  [red]x[/red]  ASCIIID did not prove it loaded {map_arg}.")
        if stdout.strip():
            console.print(stdout.strip())
        if stderr.strip():
            console.print(stderr.strip())
        return False

    proof_path = _write_map_selection("asciiid", path)
    console.print(f"  [green]✓[/green]  ASCIIID load proof recorded: {_repo_relative(proof_path)}")
    return True


def _stop_process(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def _launch_pipeline_page(
    page: str,
    *,
    open_browser: bool = True,
    hold_open: bool = False,
    duration: float | None = None,
) -> int:
    proc: subprocess.Popen[bytes] | None = None
    try:
        proc, port = _pipeline.launch_pipeline_server(port=_pipeline.find_free_port())
        _print_copyable_command(
            _pipeline.launch_command(port=port),
            cwd=REPO_ROOT,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        _pipeline.wait_for_url(f"http://127.0.0.1:{port}/", proc, timeout=10.0)

        url = f"http://127.0.0.1:{port}/{page}"
        if open_browser:
            opened = webbrowser.open(url)
            if opened:
                console.print(f"  [green]✓[/green]  Opened {url}")
            else:
                console.print(f"  [yellow]⚠[/yellow]  Browser not opened automatically. Visit: {url}")
        else:
            console.print(url)

        if duration is not None:
            time.sleep(duration)
        elif hold_open or not _can_prompt():
            console.print("  [dim]Press Ctrl-C to stop the local asset server.[/dim]")
            try:
                proc.wait()
            except KeyboardInterrupt:
                console.print("\n  [dim]Stopping local asset server...[/dim]")
        else:
            _pause("  Press Enter to stop the local asset server.")
        return 0
    except RuntimeError as exc:
        console.print(f"  [red]✗[/red]  {exc}")
        _pause("  Press Enter to continue.")
        return 1
    finally:
        if proc is not None:
            _stop_process(proc)


def _menu_xp_asset_layer2_browser() -> None:
    console.print("  Opening merged layer-2 browser. Press q in the browser to return.")
    # The browser is a full-terminal TUI.
    if _renderer.active:
        if _io_mgr.submenu_buffering:
            _flush_and_render("  Opening merged layer-2 browser...")
        with _renderer.paused():
            rc = _xp_assets_layer2_only.run_sprite_browser()
    else:
        rc = _xp_assets_layer2_only.run_sprite_browser()
    if rc != 0:
        console.print(f"  [yellow]⚠[/yellow]  merged layer-2 browser exited with code {rc}")
        _pause("  Press Enter to continue.")
    else:
        console.print("  Returned from merged layer-2 browser.")
        _pause("  Press Enter to continue.")


def _menu_xp_raw_layer_inspector() -> None:
    console.print("  Opening raw-layer XP inspector. Press q in the inspector to return.")
    if _renderer.active:
        if _io_mgr.submenu_buffering:
            _flush_and_render("  Opening raw-layer XP inspector...")
        with _renderer.paused():
            rc = _xp_raw_layer_inspector.run_raw_layer_browser()
    else:
        rc = _xp_raw_layer_inspector.run_raw_layer_browser()
    if rc != 0:
        console.print(f"  [yellow]⚠[/yellow]  raw-layer XP inspector exited with code {rc}")
        _pause("  Press Enter to continue.")
    else:
        console.print("  Returned from raw-layer XP inspector.")
        _pause("  Press Enter to continue.")


def _menu_xp_anchor_review() -> None:
    """Open the UV Body Viewer in anchor-review mode for semantic-map workflow."""
    console.print("  Opening anchor review. Press q in the viewer to return.")
    _menu_xp_uv_body_viewer()



def _git_status_porcelain_lines() -> list[str]:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip() or "git status failed")
    return [line for line in result.stdout.splitlines() if line.strip()]


def _candidate_watchdog_scope_preview(status_lines: list[str] | None = None) -> dict[str, object]:
    try:
        import watchdog_runner as canonical_mod
    except Exception as exc:
        return {
            "state": "error",
            "can_launch": False,
            "lines": [f"  [red]✗[/red]  failed to load watchdog runner: {exc}"],
            "command_args": [],
        }

    status_lines = list(status_lines) if status_lines is not None else _git_status_porcelain_lines()
    tracked_paths = sorted(canonical_mod.tracked_status_paths(status_lines))
    untracked_paths = sorted(canonical_mod.untracked_status_paths(status_lines))
    dirty_paths = tracked_paths + untracked_paths
    if not dirty_paths:
        return {
            "state": "clean",
            "can_launch": True,
            "lines": [
                "  [green]scope[/green] clean tree",
                "  [dim]No pre-run candidate scope flags needed; watchdog will see a clean worktree.[/dim]",
            ],
            "command_args": [],
        }

    labels_by_path: dict[str, list[str]] = {
        path: list(canonical_mod._scope_labels_for_path(path))
        for path in dirty_paths
    }
    unknown_paths = sorted(path for path, labels in labels_by_path.items() if not labels)
    corpus_labels: set[str] = set()
    docs_only = True
    for labels in labels_by_path.values():
        non_doc = [label for label in labels if label != "docs"]
        if non_doc:
            docs_only = False
            corpus_labels.update(non_doc)
    if unknown_paths:
        state = "unknown"
        can_launch = False
        summary = "undeclared scope"
        command_args: list[str] = []
    elif docs_only:
        state = "docs-only"
        can_launch = True
        summary = "docs-only"
        command_args = ["--intent-diff-corpus", "docs-only"]
    elif len(corpus_labels) == 1:
        normalized = next(iter(corpus_labels))
        corpus_arg = {
            "gameplay": "multiplayer-runtime",
            "watchdog": "watchdog-proof",
            "launcher": "launcher",
        }.get(normalized)
        scope = canonical_mod.resolve_diagnostic_scope(
            {"diff": {"corpora": [normalized], "paths": dirty_paths}}
        )
        state = str(scope.get("candidate_class") or normalized)
        can_launch = bool(scope.get("diagnostic"))
        summary = f"{state} ({corpus_arg})" if corpus_arg else state
        command_args = ["--intent-diff-corpus", corpus_arg] if corpus_arg else []
    else:
        state = "mixed"
        can_launch = True
        summary = "mixed local edits -> tmp clone with configurable dirty-source overlay"
        command_args = []

    if can_launch:
        lines = [f"  [green]scope[/green] {summary}"]
        if state == "mixed":
            lines.append(
                f"  [dim]{len(dirty_paths)} dirty path(s) span multiple scopes. The wrapper will leave those local edits untouched, fork a disposable tmp clone, and can overlay dirty source inputs into that clone before deploy when the operator chooses it.[/dim]"
            )
        else:
            for path in dirty_paths:
                command_args.extend(["--intent-diff-path", path])
            lines.append(
                f"  [dim]{len(dirty_paths)} dirty path(s) will define the candidate scope before reset/deploy.[/dim]"
            )
    else:
        lines = [
            f"  [red]scope[/red] {summary}",
            "  [dim]Launcher will not start Reset & Redeploy Candidate until the dirty tree maps to one diagnostic scope.[/dim]",
        ]
    if dirty_paths:
        sample = ", ".join(dirty_paths[:3])
        if len(dirty_paths) > 3:
            sample += f", +{len(dirty_paths) - 3} more"
        lines.append(f"  [dim]dirty: {sample}[/dim]")
    if unknown_paths:
        lines.append(
            f"  [dim]unmapped: {', '.join(unknown_paths[:3])}"
            + (f", +{len(unknown_paths) - 3} more" if len(unknown_paths) > 3 else "")
            + "[/dim]"
        )
    if can_launch and command_args:
        lines.append(
            "  [dim]derived flags: "
            + " ".join(command_args[:4])
            + (" ..." if len(command_args) > 4 else "")
            + "[/dim]"
        )
    elif can_launch and state == "mixed":
        lines.append(
            "  [dim]No extra scope flags will be injected here; the canonical runner will detect mixed dirty scope and switch to the committed-HEAD tmp-clone path automatically.[/dim]"
        )
    else:
        lines.append("  [dim]Use Proof Run Builder to declare an explicit scope, or clean unrelated files first.[/dim]")
    return {
        "state": state,
        "can_launch": can_launch,
        "lines": lines,
        "command_args": command_args,
        "dirty_paths": dirty_paths,
        "tracked_paths": tracked_paths,
        "untracked_paths": untracked_paths,
    }


def _bundle_candidate_proof_command(env: dict[str, str]) -> list[str]:
    preview = _candidate_watchdog_scope_preview()
    tmp_clone_source_policy = None
    if preview.get("state") == "mixed" and _can_prompt():
        _TMP_CLONE_OPTS = [
            ("p", "prompt at runtime"),
            ("o", "overlay dirty source"),
            ("c", "committed HEAD only"),
        ]
        _tc_chosen = _fuzzy_select(
            _TMP_CLONE_OPTS,
            title="Tmp-clone source policy",
            label_fn=lambda t: t[1],
            console=console,
            renderer=_renderer,
        )
        choice = _tc_chosen[0] if _tc_chosen is not None else "p"
        if choice == "o":
            tmp_clone_source_policy = "overlay-dirty-source"
        elif choice == "c":
            tmp_clone_source_policy = "committed-head-only"
        else:
            tmp_clone_source_policy = "prompt"
    cmd = _watchdog_run_command(
        env,
        mode="full",
        slot="candidate",
        tmp_clone_source_policy=tmp_clone_source_policy,
    )
    cmd.append("--commit-all-and-reset")
    extra_args = list(preview.get("command_args") or [])
    if preview.get("dirty_paths") and not preview.get("can_launch"):
        raise RuntimeError(f"candidate scope is {preview.get('state')}")
    cmd.extend(extra_args)
    return cmd


def _commit_reset_candidate_watchdog_command(env: dict[str, str]) -> list[str]:
    return _bundle_candidate_proof_command(env)


def _extend_optional_arg(cmd: list[str], flag: str, value: str | None) -> None:
    if value:
        cmd.extend([flag, value])


_NEW_MOD_SLUG_SENTINEL = "< enter new slug >"


def _choose_mod_slug() -> str:
    """Fuzzy-select an existing mod slug or prompt for a new one.

    Returns the selected/entered slug, or "" if cancelled.
    FL-3606: bundle slug needs fuzzy selector.
    """
    mod_dirs = sorted((REPO_ROOT / "mods").glob("*/")) if (REPO_ROOT / "mods").exists() else []
    if mod_dirs:
        options = [_NEW_MOD_SLUG_SENTINEL] + [m.name for m in mod_dirs]
        picked = _fuzzy_select(
            options,
            title="Select mod slug  (or pick '< enter new slug >')",
            label_fn=str,
            console=console,
            renderer=_renderer,
        )
        if picked is None:
            return ""
        if picked == _NEW_MOD_SLUG_SENTINEL:
            return _prompt_line("  New mod slug (short identifier, e.g. cool-hat)  [blank = back]", "")
        return picked
    return _prompt_line("  Mod slug (short identifier, e.g. cool-hat)  [blank = back]", "")


def _xp_animation_candidates(*, test_fixtures: bool) -> list[Path]:
    sprites_dir = ASSET_DIR / "sprites"
    candidate_dir = sprites_dir
    if not candidate_dir.exists():
        return []
    paths = sorted(path.resolve() for path in candidate_dir.glob("*.xp") if path.is_file())
    return paths


def _xp_animation_compare_default_path() -> Path:
    return RUN_DIR / "xp_animation_compare" / "latest.json"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _xp_surface_slug(path: Path) -> str:
    stem = path.stem.lower().replace("_", "-")
    if "attack" in stem:
        return "attack"
    if "plydie" in stem or "death" in stem:
        return "plydie"
    if "item-world" in stem:
        return "world"
    if "grid" in stem:
        return "inventory"
    if "hat" in stem:
        return "hat"
    if "shield" in stem:
        return "shield"
    if "weapon" in stem or "sword" in stem:
        return "weapon"
    if "body" in stem or "suit" in stem or "armour" in stem or "armor" in stem:
        return "equipped"
    return "unknown"


def _xp_candidate_by_name(candidates: list[Path], preferred_names: list[str]) -> Path | None:
    by_name = {path.name.lower(): path for path in candidates}
    for name in preferred_names:
        match = by_name.get(name.lower())
        if match is not None:
            return match
    return candidates[0] if candidates else None


def _resolve_xp_compare_slot(
    raw_value: str | None,
    candidates: list[Path],
    *,
    slot_label: str,
    default_names: list[str],
) -> tuple[Path | None, str | None]:
    if not raw_value:
        chosen = _xp_candidate_by_name(candidates, default_names)
        if chosen is None:
            return None, f"{slot_label} has no XP candidates"
        return chosen, None

    raw = raw_value.strip()
    candidate_paths = [Path(raw).expanduser()]
    if not candidate_paths[0].is_absolute():
        candidate_paths.extend([REPO_ROOT / raw, ASSET_DIR / "sprites" / raw])

    for path in candidate_paths:
        resolved = path.resolve()
        if resolved.is_file() and resolved.suffix.lower() == ".xp":
            return resolved, None

    lowered = raw.lower()
    name_matches = [path for path in candidates if path.name.lower() == lowered]
    if len(name_matches) == 1:
        return name_matches[0], None

    substring_matches = [path for path in candidates if lowered in path.name.lower()]
    if len(substring_matches) == 1:
        return substring_matches[0], None
    if len(substring_matches) > 1:
        examples = ", ".join(path.name for path in substring_matches[:8])
        suffix = f", ... {len(substring_matches) - 8} more" if len(substring_matches) > 8 else ""
        return None, f"{slot_label} selector is ambiguous: {examples}{suffix}"

    return None, f"{slot_label} selector did not match an XP file: {raw_value}"


def _xp_slot_metadata(path: Path) -> tuple[object | None, str | None]:
    try:
        xp = _xp_assets_layer2_only._load_xp_quiet(path)
        meta = _xp_assets_layer2_only._parse_metadata(xp)
    except Exception as exc:
        return None, str(exc)
    if meta is None:
        return None, "XP metadata is missing or not engine-compatible"
    return meta, None


def _xp_meta_payload(meta: object | None) -> dict[str, object] | None:
    if meta is None:
        return None
    return {
        "angles": meta.angles,
        "projs": meta.projs,
        "anim_lengths": list(meta.anim_lengths),
        "anim_sum": meta.anim_sum,
        "fr_num_x": meta.fr_num_x,
        "fr_num_y": meta.fr_num_y,
        "fr_width": meta.fr_width,
        "fr_height": meta.fr_height,
    }


def _xp_slot_payload(
    path: Path,
    *,
    role: str,
    input_rule: str,
) -> dict[str, object]:
    stat = path.stat()
    meta, parse_error = _xp_slot_metadata(path)
    payload: dict[str, object] = {
        "role": role,
        "input_rule": input_rule,
        "path": _repo_relative(path),
        "path_abs": str(path),
        "name": path.name,
        "surface": _xp_surface_slug(path),
        "sha256": _sha256_file(path),
        "size_bytes": stat.st_size,
        "mtime": int(stat.st_mtime),
        "metadata": _xp_meta_payload(meta),
    }
    if parse_error:
        payload["parse_error"] = parse_error
    return payload


def _xp_compare_field(a_meta: dict[str, object] | None, b_meta: dict[str, object] | None, field: str) -> dict[str, object]:
    a_value = a_meta.get(field) if a_meta else None
    b_value = b_meta.get(field) if b_meta else None
    return {"a": a_value, "b": b_value, "match": a_value == b_value and a_value is not None}


def _xp_compare_payload(slot_a: dict[str, object], slot_b: dict[str, object]) -> dict[str, object]:
    a_meta = slot_a.get("metadata") if isinstance(slot_a.get("metadata"), dict) else None
    b_meta = slot_b.get("metadata") if isinstance(slot_b.get("metadata"), dict) else None
    fields = {
        field: _xp_compare_field(a_meta, b_meta, field)
        for field in ("angles", "projs", "anim_lengths", "anim_sum", "fr_num_x", "fr_num_y", "fr_width", "fr_height")
    }
    metadata_available = bool(a_meta and b_meta)
    metadata_match = metadata_available and all(item["match"] for item in fields.values())
    surface_match = slot_a.get("surface") == slot_b.get("surface")
    status = "metadata-match" if metadata_match else ("metadata-different" if metadata_available else "metadata-unavailable")
    return {
        "status": status,
        "metadata_available": metadata_available,
        "metadata_match": metadata_match,
        "surface_match": surface_match,
        "fields": fields,
    }


def _write_xp_animation_compare_artifact(slot_a_arg: str | None = None, slot_b_arg: str | None = None, output: str | None = None) -> tuple[Path | None, str | None]:
    prod_candidates = _xp_animation_candidates(test_fixtures=False)
    slot_a, error = _resolve_xp_compare_slot(
        slot_a_arg,
        prod_candidates,
        slot_label="Slot A",
        default_names=["player-body.xp", "attack-body.xp", "plydie-body.xp"],
    )
    if error:
        return None, error
    assert slot_a is not None

    slot_b, error = _resolve_xp_compare_slot(
        slot_b_arg,
        prod_candidates,
        slot_label="Slot B",
        default_names=["attack-body.xp", "plydie-body.xp", "player-body.xp"],
    )
    if error:
        return None, error
    assert slot_b is not None

    slot_payload_a = _xp_slot_payload(
        slot_a,
        role="sprite_a",
        input_rule="assets/sprites/*.xp",
    )
    slot_payload_b = _xp_slot_payload(
        slot_b,
        role="sprite_b",
        input_rule="assets/sprites/*.xp",
    )

    artifact_path = Path(output).expanduser() if output else _xp_animation_compare_default_path()
    if not artifact_path.is_absolute():
        artifact_path = (REPO_ROOT / artifact_path).resolve()

    payload = {
        "contract": "launcher-xp-animation-compare-v1",
        "written_by": "scripts/launcher.py",
        "non_mutating": True,
        "mtime": int(time.time()),
        "artifact_path": _repo_relative(artifact_path),
        "catalog": {
            "slot_a_candidates": len(prod_candidates),
            "slot_b_candidates": len(prod_candidates),
        },
        "slots": {
            "a": slot_payload_a,
            "b": slot_payload_b,
        },
        "comparison": _xp_compare_payload(slot_payload_a, slot_payload_b),
    }
    _write_json_file(artifact_path, payload)
    return artifact_path, None


def _choose_xp_compare_slot(candidates: list[Path], *, slot_label: str, default_names: list[str]) -> Path | None:
    if not candidates:
        console.print(f"  [red]✗[/red]  {slot_label} has no XP candidates.")
        return None
    default_path = _xp_candidate_by_name(candidates, default_names) or candidates[0]
    return _fuzzy_select(
        candidates,
        title=f"Select {slot_label}",
        label_fn=lambda p: f"{_repo_relative(p)}{'  [default]' if p == default_path else ''}",
        path_fn=lambda p: p,
        default=default_path,
        console=console,
        renderer=_renderer,
    )


def _compare_xp_animation_slots(slot_a: str | None = None, slot_b: str | None = None, output: str | None = None) -> int:
    prod_candidates = _xp_animation_candidates(test_fixtures=False)
    console.print("  XP animation compare: two-slot design")
    console.print("  Slot A: sprite XP (assets/sprites/*.xp)")
    console.print("  Slot B: sprite XP (assets/sprites/*.xp)")
    console.print(f"  Slot A candidates: {len(prod_candidates)}")
    console.print(f"  Slot B candidates: {len(prod_candidates)}")
    if _can_prompt() and (slot_a is None or slot_b is None):
        selected_a: Path | None = None
        if slot_a is None:
            selected_a = _choose_xp_compare_slot(
                prod_candidates,
                slot_label="Slot A",
                default_names=["player-body.xp", "attack-body.xp", "plydie-body.xp"],
            )
            if selected_a is None:
                return EXIT_POLICY_BLOCKED
            slot_a = _repo_relative(selected_a)
        else:
            selected_a, _ = _resolve_xp_compare_slot(
                slot_a,
                prod_candidates,
                slot_label="Slot A",
                default_names=["player-body.xp", "attack-body.xp", "plydie-body.xp"],
            )
        if slot_b is None:
            selected_b = _choose_xp_compare_slot(
                prod_candidates,
                slot_label="Slot B",
                default_names=["attack-body.xp", "plydie-body.xp", "player-body.xp"],
            )
            if selected_b is None:
                return EXIT_POLICY_BLOCKED
            slot_b = _repo_relative(selected_b)
        if slot_a and slot_b:
            artifact_path, error = _write_xp_animation_compare_artifact(slot_a_arg=slot_a, slot_b_arg=slot_b, output=output)
            if error:
                console.print(f"  [red]x[/red]  {error}")
                return 1
            assert artifact_path is not None
            console.print(f"  Artifact: {_repo_relative(artifact_path)}")
            return _run_command(
                [
                    _repo_python(),
                    "scripts/pipeline/xp_anim_viewer.py",
                    Path(slot_a).name,
                    "--compare",
                    Path(slot_b).name,
                ],
                label="sprite animation compare",
                cwd=REPO_ROOT,
            )
    artifact_path, error = _write_xp_animation_compare_artifact(slot_a_arg=slot_a, slot_b_arg=slot_b, output=output)
    if error:
        console.print(f"  [red]x[/red]  {error}")
        return 1
    assert artifact_path is not None
    payload = _read_json_file(artifact_path) or {}
    slots = payload.get("slots") if isinstance(payload.get("slots"), dict) else {}
    slot_payload_a = slots.get("a") if isinstance(slots.get("a"), dict) else {}
    slot_payload_b = slots.get("b") if isinstance(slots.get("b"), dict) else {}
    comparison = payload.get("comparison") if isinstance(payload.get("comparison"), dict) else {}
    console.print(f"  Slot A selected: {slot_payload_a.get('path', '-')}")
    console.print(f"  Slot B selected: {slot_payload_b.get('path', '-')}")
    console.print(f"  Compare status: {comparison.get('status', 'unknown')}")
    console.print(f"  Artifact: {_repo_relative(artifact_path)}")
    return 0


def _compare_asset_xp_animations() -> int:
    candidates = _xp_animation_candidates(test_fixtures=False)
    console.print("  XP animation compare: production assets (legacy alias)")
    console.print("  Input rule: assets/sprites/*.xp")
    console.print(f"  Candidate files: {len(candidates)}")
    lines = [f"  {i+1}. {_repo_relative(path)}" for i, path in enumerate(candidates)]
    if _renderer.active:
        _ScrollView(_renderer).show_lines(lines)
    else:
        console.print("\n".join(lines))
    console.print("  [yellow]![/yellow]  blocked: output artifact contract is not specified yet.")
    return EXIT_POLICY_BLOCKED


def _compare_test_xp_animations() -> int:
    console.print("  [red]x[/red]  blocked: test XP fixtures are no longer a supported launcher surface.")
    return EXIT_POLICY_BLOCKED


SCRIPT_FAMILY_DEFS = {
    "cli-anything": {
        "title": "CLI / CLI Anything",
        "purpose": "cli-anything tools, ASCIIID automation, map/minimap/editor helpers",
        "patterns": ["docs/agent/cli-anything/*.py", "docs/agent/cli-anything/cli_anything/**/*.py"],
    },
    "deployment": {
        "title": "Deployment",
        "purpose": "candidate/current deploy, promote, reset, and web verification entry points",
        "patterns": [
            "scripts/deploy_*.py",
            "scripts/promote_candidate_to_current.py",
            "scripts/reset_*_runtime.py",
            "scripts/verify_candidate_web.py",
        ],
    },
    "multiplayer-watchdog": {
        "title": "Multiplayer / Watchdog",
        "purpose": "canonical proof wrappers, watchdog recorder, source, recipe, and run analysis",
        "patterns": [
            "scripts/watchdog*.py",
            "scripts/watchdog*.js",
            "scripts/multiplayer_visual_watchdog.js",
            "scripts/analyze_runs.py",
            "scripts/analyze_failure_log.py",
        ],
    },
    "testing-verification": {
        "title": "Testing & Verification",
        "purpose": "repo tests, verifier helpers, and focused runtime checks",
        "patterns": ["scripts/verify*.py", "scripts/test*.py", "tests/**/*.py"],
    },
    "maintenance": {
        "title": "Maintenance",
        "purpose": "maintainer automation, startup checks, guardrails, and non-gameplay hygiene",
        "patterns": [
            "scripts/maintainer/**/*.py",
            "scripts/git_guardrails.py",
            "scripts/setup_git_guardrails.sh",
        ],
    },
    "sprite-tools": {
        "title": "Sprite Tools",
        "purpose": "XP sprite sheet conversion, PNG export, and cell-accurate rendering helpers",
        "patterns": [
            "scripts/png2xp2png.py",
            "scripts/xp_to_png.py",
            "scripts/xp_to_meta.py",
            "scripts/pipeline/xp_core.py",
            "scripts/pipeline/xp_tool.py",
            "scripts/pipeline/xp_viewer.py",
            "scripts/pipeline/xp_anim_viewer.py",
            "scripts/pipeline/xp_cat.py",
            "scripts/pipeline/xp_browser.py",
        ],
    },
}


def _command_for_script(path: Path) -> str:
    rel = _repo_relative(path)
    if path.suffix == ".js":
        return f"node {rel}"
    if path.suffix == ".sh":
        return f"bash {rel}"
    if path.suffix == ".py":
        return f"{_repo_python()} {rel}"
    return rel


def _script_ux_state_for_path(path: Path) -> str:
    if path.name == "watchdog_trust_audit.py":
        return "deleted/non-authoritative"
    if path.name in {
        "analyze_failure_log.py",
        "analyze_runs.py",
        "deploy_candidate_server.py",
        "deploy_candidate_web.py",
        "deploy_current_server.py",
        "promote_candidate_to_current.py",
        "png2xp2png.py",
        "setup_addon.py",
        "watchdog_runner.py",
        "watchdog_source.py",
    }:
        return "style-compliant"
    if path.name == "multiplayer_visual_watchdog.js":
        return "machine-mode-only"
    return "open-user-ux"


def _script_family_entries(family: str) -> list[Path]:
    info = SCRIPT_FAMILY_DEFS.get(family)
    if info is None:
        return []
    seen: set[Path] = set()
    entries: list[Path] = []
    for pattern in info["patterns"]:
        for path in REPO_ROOT.glob(pattern):
            if not path.is_file():
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            entries.append(resolved)
    return sorted(entries, key=lambda path: _repo_relative(path))


def _list_script_family(family: str, *, pause_on_success: bool = False) -> int:
    info = SCRIPT_FAMILY_DEFS.get(family)
    if info is None:
        console.print(f"  [red]x[/red]  unknown script family: {family}")
        return 1
    entries = _script_family_entries(family)
    lines = [
        f"Dev Tool Scripts: {info['title']}",
        f"purpose: {info['purpose']}",
        "status: inventory shell; audited script_ux_state values are per script, unaudited scripts remain open-user-ux",
        f"count: {len(entries)}",
        "",
    ]
    for path in entries[:80]:
        lines.extend(
            [
                _repo_relative(path),
                f"  command: {_command_for_script(path)}",
                f"  script_ux_state: {_script_ux_state_for_path(path)}",
            ]
        )
    if len(entries) > 80:
        lines.append(f"... {len(entries) - 80} more scripts omitted")
    _write_stdout_lines(lines)
    if pause_on_success:
        _pause("  Press Enter to continue.")
    return 0


def _show_workbench_help(*, pause_on_success: bool = False) -> int:
    _write_stdout_lines(
        [
            "Workbench Help",
            "status: local scripts launcher handoff; canonical launcher owner",
            f"run: {_repo_python()} scripts/launcher.py --action asset-workbench",
            f"no-browser: {_repo_python()} scripts/launcher.py --action asset-workbench --no-browser",
            "owner: local scripts.pipeline web server until replacement backend contract is adopted",
        ]
    )
    if pause_on_success:
        _pause("  Press Enter to continue.")
    return 0


def _asset_menu_blend_file(run_paths: dict[str, Path] | None = None) -> str:
    default = str((run_paths or _osm_run_paths())["blend_file"])
    if not _can_prompt():
        return default
    picked = _file_picker(Path.home(), filter_glob="*.blend", title="Select Blender .blend")
    return str(picked) if picked else default


# ── Bounded prompt helpers ─────────────────────────────────────────────────────


def _choose_max_players() -> int | None:
    """Fuzzy-select a max-player count (bounded 1–16).

    Returns the chosen integer, or None if cancelled.
    FL-3480: bounded set prompt — max players.
    """
    options = [str(n) for n in range(1, 17)]
    default_label = str(_local_host_max_players())
    picked = _fuzzy_select(
        options,
        title=f"Max players  (current: {default_label})",
        label_fn=str,
        default=default_label,
        console=console,
        renderer=_renderer,
    )
    if picked is None:
        return None
    return int(picked)


def _choose_grid_size(*, default: str = "1") -> int | None:
    """Fuzzy-select a grid-size integer (bounded 1–50).

    Returns the chosen integer, or None if cancelled.
    FL-3480: bounded set prompt — grid size.
    """
    options = [str(n) for n in range(1, 51)]
    default_label = default
    picked = _fuzzy_select(
        options,
        title=f"Grid size  (default: {default_label})",
        label_fn=str,
        default=default_label,
        console=console,
        renderer=_renderer,
    )
    if picked is None:
        return None
    return int(picked)


def _choose_material_id(*, default: str = "1") -> int | None:
    """Fuzzy-select a material ID (bounded 1–999).

    Returns the chosen integer, or None if cancelled.
    FL-3480: bounded set prompt — material ID.
    """
    options = [str(n) for n in range(1, 1000)]
    default_label = default
    picked = _fuzzy_select(
        options,
        title=f"Material ID  (default: {default_label})",
        label_fn=str,
        default=default_label,
        console=console,
        renderer=_renderer,
    )
    if picked is None:
        return None
    return int(picked)


def _asset_menu_meshes_dir(run_paths: dict[str, Path] | None = None) -> str:
    """Prompt for a mesh output directory, defaulting to the run's meshes dir.

    Uses _file_picker when interactive; falls back to the default path otherwise.
    """
    default = str((run_paths or _osm_run_paths())["meshes_dir"])
    if not _can_prompt():
        return default
    picked = _file_picker(Path(default), filter_glob=None, title="Select mesh output dir")
    return str(picked) if picked else default


def _asset_menu_a3d_output(run_paths: dict[str, Path] | None = None) -> str:
    """Prompt for an A3D output path, defaulting to the run's a3d_output.

    Uses _file_picker when interactive; falls back to the default path otherwise.
    """
    default = str((run_paths or _osm_run_paths())["a3d_output"])
    if not _can_prompt():
        return default
    picked = _file_picker(Path(default), filter_glob=None, title="Select A3D output path")
    return str(picked) if picked else default


def _osm_runs_root() -> Path:
    return ASSET_DIR / "meshes" / "osm_runs"


def _osm_active_mesh_root_file() -> Path:
    return _osm_runs_root() / ".active_mesh_root"


def _new_osm_run_id() -> str:
    return time.strftime("launcher_osm_%Y%m%d_%H%M%S")


def _osm_run_paths(run_id: str | None = None) -> dict[str, Path]:
    run_root = _osm_runs_root() / (run_id or _new_osm_run_id())
    return {
        "run_id": Path(run_root).name,
        "run_root": run_root,
        "blend_file": run_root / "workspace.blend",
        "meshes_dir": run_root / "meshes",
        "a3d_output": run_root / "output.a3d",
    }


def _asciiid_bin_path() -> Path:
    return RUN_DIR / "asciiid"


def _baked_osm_ready() -> bool:
    return _asciiid_bin_path().exists()


def _print_blender_osm_prereqs() -> None:
    status = _blender_paths.probe()
    cfg = _lcfg.load()
    addon_missing = [addon for addon in status.required_addons if not status.addons.get(addon, False)]
    addon_state = "ok" if status.blender_path and not addon_missing else "missing"
    console.print(
        "  Setup: "
        f"Blender {'ok' if status.blender_path else 'missing'}; "
        f"required addons {addon_state}; "
        f"blosm {'ok' if status.blosm_available else 'missing'}; "
        f"BLOSM_API_KEY {'set' if cfg.get('BLOSM_API_KEY') else 'missing'}; "
        f"asciiid {'ok' if _baked_osm_ready() else 'missing'}."
    )
    if not cfg.get("BLOSM_API_KEY"):
        console.print("  [dim]Set key via Blender Config before using online modes.[/dim]")
    console.print("  Online = needs blosm (Blender OpenStreetMap addon) + BLOSM_API_KEY.")
    console.print("  Local = .osm or .blend file. Local pre-processed = .osm only, uses ASCIIID editor for terrain bake.")
    console.print("  Outputs land under assets/meshes/osm_runs/<run_id>/ as workspace.blend, meshes/, output.a3d, and run metadata.")
    console.print()


def _build_osm_online_commands(blend_file: str, bbox: dict[str, str], meshes_dir: str, a3d_output: str) -> list[list[str]]:
    run_root = Path(blend_file).expanduser().resolve().parent
    return [[
        _repo_python(),
        "scripts/sbu_e2e_run.py",
        "--min-lat", bbox["min_lat"],
        "--max-lat", bbox["max_lat"],
        "--min-lon", bbox["min_lon"],
        "--max-lon", bbox["max_lon"],
        "--pipeline-mode", "traditional",
        "--run-root", str(run_root),
        "--blend-file", blend_file,
        "--meshes-dir", meshes_dir,
        "--a3d-output", a3d_output,
    ]]


def _build_osm_local_commands(
    local_path: str,
    blend_file: str,
    meshes_dir: str,
    a3d_output: str,
    *,
    pipeline_mode: str = "traditional",
    no_topology_bake: bool = False,
) -> list[list[str]]:
    path = Path(local_path).expanduser().resolve()
    if path.suffix.lower() == ".osm":
        run_root = Path(blend_file).expanduser().resolve().parent
        cmd = [
            _repo_python(), "scripts/sbu_e2e_run.py",
            "--osm-file", str(path),
            "--run-root", str(run_root),
            "--meshes-dir", meshes_dir,
            "--a3d-output", a3d_output,
            "--pipeline-mode", pipeline_mode,
        ]
        if no_topology_bake:
            cmd.append("--no-topology-bake")
        return [cmd]

    if pipeline_mode != "traditional":
        raise ValueError("Baked OSM mode only supports a local .osm input. Use the traditional local/export path for .blend files.")

    run_root = Path(a3d_output).expanduser().resolve().parent
    return [[
        _repo_python(),
        "scripts/sbu_e2e_run.py",
        "--blend-file", str(path),
        "--run-root", str(run_root),
        "--meshes-dir", meshes_dir,
        "--a3d-output", a3d_output,
        "--pipeline-mode", "traditional",
        "--skip-import",
    ]]


def _build_osm_export_command(blend_file: str, run_root: Path, meshes_dir: str, a3d_output: str) -> list[str]:
    return [
        _repo_python(),
        "scripts/sbu_e2e_run.py",
        "--blend-file",
        blend_file,
        "--run-root",
        str(run_root),
        "--meshes-dir",
        meshes_dir,
        "--a3d-output",
        a3d_output,
        "--pipeline-mode",
        "baked",
        "--skip-import",
    ]


def _build_osm_resume_command(run_root: Path, resume_map: Path, meshes_dir: str | None, a3d_output: str | None) -> list[str]:
    command = [
        _repo_python(),
        "scripts/sbu_e2e_run.py",
        "--pipeline-mode",
        "baked",
        "--run-root",
        str(run_root),
        "--resume-fixtures-from",
        str(resume_map),
    ]
    if meshes_dir:
        command.extend(["--meshes-dir", meshes_dir])
    if a3d_output:
        command.extend(["--a3d-output", a3d_output])
    return command


def _run_osm_commands(commands: list[list[str]], *, pause_on_success: bool = False) -> int:
    env = _tool_env(str(REPO_ROOT / "docs/agent/cli-anything"))
    for index, command in enumerate(commands):
        rc = _run_command(
            command,
            env=env,
            cwd=REPO_ROOT,
            pause_on_success=pause_on_success and index == len(commands) - 1,
        )
        if rc != 0:
            return rc
    return 0


def _run_sbu_sac_verify(*, no_open: bool = False, detach_open: bool = False) -> int:
    command = [_repo_python(), "scripts/sbu_sac_verify_run.py"]
    if no_open:
        command.append("--no-open")
    if detach_open:
        command.append("--detach-open")
    return _run_command(command, label="temporary Student Activities Center OSM verify", cwd=REPO_ROOT)


def _run_sbu_verify_building(args: argparse.Namespace | None = None, *, pause_on_success: bool = False) -> int:
    run_id = getattr(args, "run_id", None) if args is not None else None
    building = getattr(args, "building", None) if args is not None else None
    bbox = getattr(args, "bbox", None) if args is not None else None
    if not run_id and _can_prompt():
        _all_runs = _osm_run_dirs()[:20]
        if _all_runs:
            _picked = _fuzzy_select(
                _all_runs,
                title="Select OSM run",
                label_fn=lambda r: f"{r.name}  {'✓ output.a3d' if (r / 'output.a3d').exists() else '(incomplete)'}",
                path_fn=lambda r: r,
                default=_all_runs[0],
                console=console,
                renderer=_renderer,
            )
            run_id = _picked.name if _picked is not None else ""
        else:
            run_id = _prompt_line("  OSM run ID (e.g. launcher_osm_20260428_142533)", "")
    if not building and _can_prompt():
        building = _prompt_line("  Building name or ID to verify (e.g. Student Activities Center)", "Student Activities Center")
    if not building:
        console.print("  [red]✗[/red]  --building is required.")
        _print_copyable_command(
            [_repo_python(), "scripts/sbu_verify_building.py", "--run-id", "<run-id>", "--building", "<name-or-id>"],
            cwd=REPO_ROOT,
        )
        return 1
    command = [_repo_python(), "scripts/sbu_verify_building.py", "--building", str(building)]
    if run_id:
        command.extend(["--run-id", str(run_id)])
    if bbox:
        command.extend(["--bbox", str(bbox)])
    rc = _run_command(command, label="OSM building verification", cwd=REPO_ROOT)
    if pause_on_success and rc == 0:
        _pause("  Press Enter to continue.")
    return rc


def _validate_osm_bbox(bbox: dict[str, str]) -> str | None:
    try:
        min_lat = float(bbox["min_lat"])
        max_lat = float(bbox["max_lat"])
        min_lon = float(bbox["min_lon"])
        max_lon = float(bbox["max_lon"])
    except (KeyError, TypeError, ValueError):
        return "Bounding box values must be numeric."
    if min_lat >= max_lat:
        return "Bounding box requires min latitude < max latitude."
    if min_lon >= max_lon:
        return "Bounding box requires min longitude < max longitude."
    lat_span = max_lat - min_lat
    lon_span = max_lon - min_lon
    if lat_span > 0.25 or lon_span > 0.25 or (lat_span * lon_span) > 0.05:
        return "Bounding box looks too large; reduce the area before running the pipeline."
    return None


def _prompt_osm_bbox() -> dict[str, str] | None:
    bbox = {
        "min_lat": _prompt_line("  Min latitude (e.g. 40.7580)", ""),
        "max_lat": _prompt_line("  Max latitude (e.g. 40.7700)", ""),
        "min_lon": _prompt_line("  Min longitude (e.g. -73.9850)", ""),
        "max_lon": _prompt_line("  Max longitude (e.g. -73.9700)", ""),
    }
    error = _validate_osm_bbox(bbox)
    if error:
        console.print(f"  [red]✗[/red]  {error}")
        _pause("  Press Enter to continue.")
        return None
    return bbox


def _show_osm_past_runs() -> Path | None:
    runs = _osm_run_dirs()[:20]
    if not runs:
        console.print("  [yellow]⚠[/yellow]  No OSM runs found. Start a pipeline run first (Blender & OSM).")
        return None
    return _fuzzy_select(
        runs,
        title="Select OSM past run",
        label_fn=lambda r: f"{r.name}  {'✓ output.a3d' if (r / 'output.a3d').exists() else '(incomplete)'}",
        path_fn=lambda r: r,
        console=console,
        renderer=_renderer,
    )


def _latest_osm_resume_run() -> Path | None:
    for run in _osm_run_dirs():
        if (run / "fixture_instances.json").exists():
            return run
    return None


def _resolve_osm_resume_run(run_id: str | None, resume_map: Path) -> Path | None:
    if run_id:
        return _osm_runs_root() / run_id
    if (resume_map.parent / "fixture_instances.json").exists():
        return resume_map.parent
    return None


def _action_osm_resume(args: argparse.Namespace | None = None, *, pause_on_success: bool = False) -> int:
    run_id = getattr(args, "run_id", None) if args is not None else None
    local_path = getattr(args, "local_path", None) if args is not None else None
    map_path = getattr(args, "map_path", None) if args is not None else None
    meshes_dir_arg = getattr(args, "meshes_dir", None) if args is not None else None
    a3d_output_arg = getattr(args, "a3d_output", None) if args is not None else None

    resume_path_text = local_path or map_path
    if _can_prompt():
        latest = _latest_osm_resume_run()
        _all_runs = _osm_run_dirs()[:20]
        _default_run_obj = latest or (_all_runs[0] if _all_runs else None)
        _picked_run = _fuzzy_select(
            _all_runs,
            title="Select OSM run to resume",
            label_fn=lambda r: f"{r.name}  {'✓ output.a3d' if (r / 'output.a3d').exists() else '(incomplete)'}",
            path_fn=lambda r: r,
            default=_default_run_obj,
            console=console,
            renderer=_renderer,
        )
        if _picked_run is not None:
            run_id = run_id or _picked_run.name
            if not resume_path_text:
                resume_path_text = str(_picked_run / "output.a3d")

    if not resume_path_text:
        console.print("  [red]✗[/red]  map-osm-resume requires --local-path <baked-map.a3d> or --map <baked-map.a3d>.")
        return 1

    resume_map = Path(resume_path_text).expanduser().resolve()
    if not resume_map.exists():
        console.print(f"  [red]✗[/red]  resume map not found: {resume_map}")
        return 1
    if resume_map.suffix.lower() != ".a3d":
        console.print("  [red]✗[/red]  map-osm-resume requires a baked .a3d resume map.")
        return 1

    run_root = _resolve_osm_resume_run(run_id, resume_map)
    if run_root is None:
        console.print("  [red]✗[/red]  map-osm-resume requires --run-id when the resume map is outside an OSM run folder.")
        return 1
    if not run_root.exists():
        console.print(f"  [red]✗[/red]  OSM run not found: {run_root}")
        return 1
    fixture_specs = run_root / "fixture_instances.json"
    if not fixture_specs.exists():
        console.print(f"  [red]✗[/red]  missing deferred fixture specs: {fixture_specs}")
        return 1

    meshes_dir = meshes_dir_arg or str(run_root / "meshes")
    a3d_output = a3d_output_arg or str(run_root / "output.a3d")
    command = _build_osm_resume_command(run_root, resume_map, meshes_dir, a3d_output)
    return _run_osm_commands([command], pause_on_success=pause_on_success)


def _print_multiplayer_config_noninteractive() -> int:
    console.rule("\u26a0 Multiplayer Settings", style="dim")
    env = _senv.load()
    console.print("  Current multiplayer config (non-interactive):")
    if not env:
        console.print("  [yellow]⚠[/yellow]  No server.env found. Starter values the Multiplayer Wizard will offer on first run:")
        env = _senv.canonical_defaults()
    output_lines: list[str] = []
    for key, value in sorted(env.items()):
        if key in _senv.HIDDEN_FIELDS and value:
            value = "(hidden)"
        output_lines.append(f"{key}={value}")
    _write_stdout_lines(output_lines)
    return 0


def _print_blender_config_noninteractive() -> int:
    status = _blender_paths.probe()
    cfg = _lcfg.load()
    console.print("  Blender & OpenStreetMap config (non-interactive):")
    addon_profile = getattr(status, "addon_profile", "") or ""
    required_addons = tuple(getattr(status, "required_addons", ()) or ())
    legacy_addons = dict(getattr(status, "legacy_addons", {}) or {})
    required = ",".join(required_addons)
    legacy_present = ",".join(name for name, present in legacy_addons.items() if present)
    _write_stdout_lines([
        f"BLENDER_PATH={status.blender_path or ''}",
        f"BLENDER_VERSION={status.version or ''}",
        f"BLENDER_ADDON_PROFILE={addon_profile}",
        f"BLENDER_REQUIRED_ADDONS={required}",
        f"BLENDER_LEGACY_286_ADDONS_PRESENT={legacy_present}",
        f"BLOSM_API_KEY={'(set)' if cfg.get('BLOSM_API_KEY') else ''}",
        f"BLOSM_ADDON={'yes' if status.blosm_available else ''}",
        f"ASCIIID_BIN={'yes' if _baked_osm_ready() else ''}",
    ])
    return 0


def _run_goto(target: str) -> int:
    if target in {"3.1", "4.1"}:
        label = "CONFIG & STATUS -> Multiplayer"
        interactive = _edit_multiplayer_settings
        noninteractive = _print_multiplayer_config_noninteractive
    elif target in {"3.2", "4.2"}:
        label = "CONFIG & STATUS -> Blender & OpenStreetMap Config"
        interactive = _edit_blender_paths
        noninteractive = _print_blender_config_noninteractive
    elif target == "2.2":
        label = "ASSET & MAP EDITOR -> List Maps"
        interactive = _menu_list_maps
        noninteractive = None
    elif target in {"2.5.b", "2.4.b", "2.6"}:
        label = "ASSET & MAP EDITOR -> Dev Tool Scripts -> Blender & OpenStreetMap"
        interactive = _menu_blender_osm
        noninteractive = None
    elif target == "1.2.2":
        label = "GAME -> Multiplayer -> Host"
        interactive = _menu_multiplayer_host
        noninteractive = None
    else:
        console.print(f"  [red]✗[/red]  Unknown goto target: {target}")
        return 1

    if not _can_prompt():
        if noninteractive is None:
            console.print(f"  [yellow]⚠[/yellow]  goto:{target} requires an interactive TTY ({label}).")
            return EXIT_POLICY_BLOCKED
        return noninteractive()

    interactive()
    return 0


def _menu_blender_osm() -> None:
    while True:
        _draw_submenu_header("Blender & OpenStreetMap (OSM)")
        _print_blender_osm_prereqs()
        _blosm_cfg = _lcfg.load()
        _blosm_key_ok = bool(_blosm_cfg.get("BLOSM_API_KEY"))
        _l_suffix = "" if _blosm_key_ok else "  (API key missing)"
        _menu_line(f"  [l] New Map From Location{_l_suffix}", suffix_markup="[dim]Download OSM data by lat/lon bounding box and build a new map[/dim]")
        _menu_line("  [o] New Map From .osm", suffix_markup="[dim]Import a local .osm file[/dim]")
        _menu_line("  [b] New Pre-processed Map (.osm + ASCIIID terrain bake)", suffix_markup="[dim]Import .osm and run editor terrain processing[/dim]")
        _menu_line("  [p] Process .blend", suffix_markup="[dim]Import a .blend file and bake to ASCIIID terrain[/dim]")
        _menu_line("  [r] Resume", suffix_markup="[dim]Continue an interrupted pipeline run[/dim]")
        _menu_line("  [h] Past Runs", suffix_markup="[dim]View history of past OSM import runs and their outputs[/dim]")
        _menu_line("  [v] Verify OSM Building", suffix_markup="[dim]Verify a specific OSM building rendered correctly[/dim]")
        _menu_line("  [c] Blender Config", suffix_markup="[dim]Configure Blender paths and addon settings[/dim]")
        _menu_line("  [m] Mesh Inventory", suffix_markup="[dim]List fixture AKMs, curated root meshes, and quarantined meshes[/dim]")
        _menu_line("  [q] Back")
        choice = _prompt_char("> ")

        if choice == "q":
            return

        if choice == "c":
            _run_goto("3.2")
            continue

        if choice == "m":
            _menu_meshes()
            continue

        if choice == "l":
            cfg = _lcfg.load()
            blender = _blender_paths.probe()
            if not blender.blosm_available or not cfg.get("BLOSM_API_KEY"):
                console.print("  [yellow]⚠[/yellow]  Online Blosm import requires the blosm addon and a BLOSM_API_KEY in .asciicker.conf.")
                console.print("  Get an ArcGIS access token at https://location.arcgis.com/sign-up/ and paste it into BLOSM_API_KEY.")
                console.print("  Use [3] CONFIG & STATUS -> [2] Blender & OSM Config to fix this, or choose the local map/project flow.")
                _pause("  Press Enter to continue.")
                continue

            run_paths = _osm_run_paths()
            blend_file = _asset_menu_blend_file(run_paths)
            run_root = Path(blend_file).expanduser().parent
            console.print(f"  Run root: {run_root}")
            bbox = _prompt_osm_bbox()
            if bbox is None:
                continue
            meshes_dir = _asset_menu_meshes_dir(run_paths)
            a3d_output = _asset_menu_a3d_output(run_paths)
            rc = _run_osm_commands(
                _build_osm_online_commands(blend_file, bbox, meshes_dir, a3d_output),
                pause_on_success=True,
            )
            if rc == 0:
                console.print()
                console.print("  [bold]✓[/bold] Pipeline run ended — verify output before trusting results.")
                console.print(f"  [cyan]output:[/cyan]  {a3d_output}")
                console.print("  [yellow]next:[/yellow]   Open in ASCIIID or use [bold][v][/bold] Verify OSM Building to check results.")
            continue

        if choice == "o":
            picked = _file_picker(Path.home() / "Downloads", filter_glob="*.osm", title="Select .osm file") if _can_prompt() else None
            local_path = str(picked) if picked else ""
            resolved = Path(local_path).expanduser()
            if not resolved.exists():
                console.print(f"  [red]✗[/red]  path not found: {resolved}")
                _pause("  Press Enter to continue.")
                continue
            if resolved.suffix.lower() != ".osm":
                console.print("  [red]✗[/red]  New Map From .osm requires a local .osm input.")
                _pause("  Press Enter to continue.")
                continue
            run_paths = _osm_run_paths()
            blend_file = _asset_menu_blend_file(run_paths)
            console.print(f"  Run root: {Path(blend_file).expanduser().parent}")
            meshes_dir = _asset_menu_meshes_dir(run_paths)
            a3d_output = _asset_menu_a3d_output(run_paths)
            rc = _run_osm_commands(
                _build_osm_local_commands(str(resolved), blend_file, meshes_dir, a3d_output),
                pause_on_success=True,
            )
            if rc == 0:
                console.print()
                console.print("  [bold]✓[/bold] Pipeline run ended — verify output before trusting results.")
                console.print(f"  [cyan]output:[/cyan]  {a3d_output}")
                console.print("  [yellow]next:[/yellow]   Open in ASCIIID or use [bold][v][/bold] Verify OSM Building to check results.")
            continue

        if choice == "b":
            if not _baked_osm_ready():
                console.print("  [red]✗[/red]  ASCIIID editor binary not built — run: make editor")
                answer = _prompt_char("  Build it now? [y/N] ")
                if answer == "y":
                    _run_command(["make", "-C", str(REPO_ROOT), "editor"], label="build asciiid", cwd=REPO_ROOT)
                _pause("  Press Enter to continue.")
                continue
            picked = _file_picker(Path.home() / "Downloads", filter_glob="*.osm", title="Select .osm file") if _can_prompt() else None
            local_path = str(picked) if picked else ""
            resolved = Path(local_path).expanduser()
            if not resolved.exists():
                console.print(f"  [red]✗[/red]  path not found: {resolved}")
                _pause("  Press Enter to continue.")
                continue
            if resolved.suffix.lower() != ".osm":
                console.print("  [red]✗[/red]  Local baked mode only supports a local .osm input.")
                _pause("  Press Enter to continue.")
                continue
            run_paths = _osm_run_paths()
            blend_file = _asset_menu_blend_file(run_paths)
            console.print(f"  Run root: {Path(blend_file).expanduser().parent}")
            meshes_dir = _asset_menu_meshes_dir(run_paths)
            a3d_output = _asset_menu_a3d_output(run_paths)
            topo_skip = _prompt_char("  Skip topology bake? (recommended for debug) [y/N] ")
            rc = _run_osm_commands(
                _build_osm_local_commands(
                    str(resolved),
                    blend_file,
                    meshes_dir,
                    a3d_output,
                    pipeline_mode="baked",
                    no_topology_bake=(topo_skip == "y"),
                ),
                pause_on_success=True,
            )
            if rc == 0:
                console.print()
                console.print("  [bold]✓[/bold] Pipeline run ended — verify output before trusting results.")
                console.print(f"  [cyan]output:[/cyan]  {a3d_output}")
                console.print("  [yellow]next:[/yellow]   Open in ASCIIID or use [bold][v][/bold] Verify OSM Building to check results.")
            continue

        if choice == "p":
            run_paths = _osm_run_paths()
            picked = _file_picker(Path.home(), filter_glob="*.blend", title="Select Blender .blend") if _can_prompt() else None
            blend_file = str(picked) if picked else ""
            resolved = Path(blend_file).expanduser()
            if not resolved.exists():
                console.print(f"  [red]✗[/red]  path not found: {resolved}")
                _pause("  Press Enter to continue.")
                continue
            meshes_dir = _asset_menu_meshes_dir(run_paths)
            a3d_output = _asset_menu_a3d_output(run_paths)
            _run_osm_commands(
                [_build_osm_export_command(str(resolved), run_paths["run_root"], meshes_dir, a3d_output)],
                pause_on_success=True,
            )
            continue

        if choice == "r":
            _action_osm_resume(pause_on_success=True)
            continue

        if choice == "h":
            selected = _show_osm_past_runs()
            if selected is not None:
                _action_osm_resume(
                    argparse.Namespace(run_id=selected.name, local_path=None, map_path=None, meshes_dir=None, a3d_output=None),
                    pause_on_success=True,
                )
            continue

        if choice == "v":
            _run_sbu_verify_building(pause_on_success=True)
            continue

        console.print(f"  [dim]Unknown key: {choice!r}[/dim]")


def _mesh_inventory() -> list[Path]:
    return sorted((ASSET_DIR / "meshes").glob("*.akm"))


def _fixture_inventory() -> list[Path]:
    return sorted((ASSET_DIR / "meshes" / "fixtures").glob("*.akm"))


def _fixture_alias_inventory() -> list[Path]:
    return sorted(p for p in (ASSET_DIR / "meshes").glob("*.akm") if p.is_symlink())


def _root_mesh_inventory() -> list[Path]:
    return sorted(p for p in (ASSET_DIR / "meshes").glob("*.akm") if not p.is_symlink())


def _quarantine_mesh_inventory() -> list[Path]:
    q = ASSET_DIR / "meshes" / "quarantine"
    return sorted(q.rglob("*.akm")) if q.exists() else []


def _osm_run_dirs() -> list[Path]:
    root = _osm_runs_root()
    return sorted((p for p in root.iterdir() if p.is_dir()), reverse=True) if root.exists() else []


def _active_osm_mesh_root() -> Path | None:
    pointer = _osm_active_mesh_root_file()
    if not pointer.exists():
        return None
    raw = pointer.read_text(encoding="utf-8").strip()
    if not raw:
        return None
    path = (REPO_ROOT / raw).resolve() if not Path(raw).is_absolute() else Path(raw)
    return path


def _menu_meshes() -> None:
    while True:
        _draw_submenu_header("Meshes")
        fixtures = _fixture_inventory()
        aliases = _fixture_alias_inventory()
        curated = _root_mesh_inventory()
        quarantined = _quarantine_mesh_inventory()
        runs = _osm_run_dirs()
        active_root = _active_osm_mesh_root()
        active_label = str(active_root) if active_root else "(none)"
        console.print(f"  Fixture source dir: {ASSET_DIR / 'meshes' / 'fixtures'}")
        console.print(f"  Fixture source AKMs: {len(fixtures)}")
        console.print(f"  Fixture alias symlinks: {len(aliases)}")
        console.print(f"  Curated root meshes: {len(curated)}")
        console.print(f"  Quarantined meshes: {len(quarantined)}")
        console.print(f"  OSM run folders: {len(runs)}")
        console.print(f"  Active OSM mesh root: {active_label}")
        console.print()
        _menu_line("  [1] List Fixtures   source fixture AKMs only", suffix_markup="[dim]Show all .akm fixture mesh files[/dim]")
        _menu_line("  [q] Back")
        choice = _prompt_char("> ")

        if choice == "q":
            return
        if choice == "1":
            if not fixtures:
                console.print("  [yellow]⚠[/yellow]  No .akm fixtures found. Import fixtures via the asset pipeline.")
            else:
                for fixture in fixtures:
                    console.print(f"  {fixture.name}")
            _pause("  Press Enter to continue.")
            continue
        console.print(f"  [dim]Unknown key: {choice!r}[/dim]")


def _menu_map_diagnostics() -> None:
    while True:
        _draw_submenu_header("Map Diagnostics")
        _menu_line("  [a] Inspect Map     audit A3D structure", suffix_markup="[dim]Audit internal map file structure[/dim]")
        _menu_line("  [b] Validate Map    binary format check", suffix_markup="[dim]Check binary format of a map file[/dim]")
        _menu_line("  [c] Edit Instances  list / delete map instances", suffix_markup="[dim]List or delete placed objects[/dim]")
        _menu_line("  [d] New Test Map    generate a flat deterministic A3D", suffix_markup="[dim]Create a minimal flat test map[/dim]")
        _menu_line("  [q] Back")
        choice = _prompt_char("> ")

        if choice == "q":
            return
        if choice == "a":
            picked = _file_picker(ASSET_DIR / "a3d", filter_glob="*.a3d", title="Select A3D map") if _can_prompt() else None
            map_path = str(picked) if picked else str(DEFAULT_MAP)
            extra = _prompt_line("  Extra inspect flags (e.g. --verbose; blank for none)", "")
            _run_map_inspect(map_path, extra.split() if extra else [])
            continue
        if choice == "b":
            picked = _file_picker(ASSET_DIR / "a3d", filter_glob="*.a3d", title="Select A3D map") if _can_prompt() else None
            map_path = str(picked) if picked else str(DEFAULT_MAP)
            _run_map_validate(map_path.split())
            continue
        if choice == "c":
            picked = _file_picker(ASSET_DIR / "a3d", filter_glob="*.a3d", title="Select A3D map") if _can_prompt() else None
            map_path = str(picked) if picked else str(DEFAULT_MAP)
            _action_chosen = _fuzzy_select(
                ["list", "delete"],
                title="Instance action",
                label_fn=str,
                console=console,
                renderer=_renderer,
            )
            action = _action_chosen if _action_chosen is not None else "list"
            if action == "delete":
                pattern = _prompt_line("  Match pattern (glob, e.g. fixture_* or building_001)", "")
                if not pattern:
                    console.print("  [red]✗[/red]  Delete requires a match pattern (e.g. fixture_* or building_001).")
                    _pause("  Press Enter to continue.")
                    continue
                _run_instance_delete(map_path, pattern)
            else:
                _run_instance_list(map_path)
            continue
        if choice == "d":
            picked = _file_picker(ASSET_DIR / "a3d", filter_glob="*.a3d", title="Select output .a3d location") if _can_prompt() else None
            out_path = str(picked) if picked else str(ASSET_DIR / "a3d" / "minimal_1x1.a3d")
            grid = _choose_grid_size(default="1")
            material = _choose_material_id(default="1")
            if grid is None or material is None:
                console.print("  [dim]Cancelled.[/dim]")
                continue
            _run_new_test_map(out_path, str(grid), str(material))
            continue
        console.print(f"  [dim]Unknown key: {choice!r}[/dim]")


def _available_a3d_maps() -> list[Path]:
    return sorted(path.resolve() for path in (ASSET_DIR / "a3d").glob("*.a3d") if path.is_file())


def _map_preview_command(path: Path) -> str:
    return f"{_repo_python()} docs/agent/cli-anything/minimap_render.py --map {_repo_relative(path)}"


def _map_browser_lines(*, selected: Path | None = None, limit: int | None = None) -> list[str]:
    maps = _available_a3d_maps()
    selected_path = (selected or _SELECTED_MAP_PATH)
    if selected_path is not None:
        selected_path = selected_path.resolve()

    lines = [
        "List Maps",
        "discovery: assets/a3d/*.a3d",
        "preview source: docs/agent/cli-anything/minimap_render.py",
        "select: enter any row number, including 10+",
        f"selected: {_repo_relative(selected_path) if selected_path else '(none)'}",
        "",
    ]
    if not maps:
        lines.append("No .a3d maps found under assets/a3d.")
        return lines

    visible_maps = maps[:limit] if limit is not None else maps
    for idx, path in enumerate(visible_maps, 1):
        stat = path.stat()
        tags: list[str] = []
        if path == DEFAULT_MAP.resolve():
            tags.append("default")
        if selected_path is not None and path == selected_path:
            tags.append("selected")
        tag_text = f" [{' '.join(tags)}]" if tags else ""
        lines.append(f"{idx:2}. {_repo_relative(path)} size={stat.st_size}{tag_text}")
    if limit is not None and len(maps) > limit:
        lines.append(f"... {len(maps) - limit} more maps omitted")
    return lines


def _render_map_browser_table(*, limit: int | None = 20) -> None:
    maps = _available_a3d_maps()
    if not maps:
        console.print("  [yellow]![/yellow]  No .a3d maps found under assets/a3d.")
        return

    selected = _SELECTED_MAP_PATH.resolve() if _SELECTED_MAP_PATH else None
    table = Table(show_header=True, header_style="bold")
    table.add_column("#", justify="right", no_wrap=True)
    table.add_column("Map")
    table.add_column("Size", justify="right")
    table.add_column("State")
    visible_maps = maps[:limit] if limit is not None else maps
    for idx, path in enumerate(visible_maps, 1):
        tags: list[str] = []
        if path == DEFAULT_MAP.resolve():
            tags.append("default")
        if selected is not None and path == selected:
            tags.append("selected")
        table.add_row(
            str(idx),
            _repo_relative(path),
            str(path.stat().st_size),
            ", ".join(tags) if tags else "\u2014",
        )
    console.print(table)
    if limit is not None and len(maps) > limit:
        console.print(f"  [dim]... {len(maps) - limit} more maps omitted[/dim]")
    console.print("  State: default = local game default; selected = active selection; — = not default.")


def _browse_minimap_maps(map_path: str | None = None, *, pause_on_success: bool = False, action_menu: bool = False) -> int:
    # Non-interactive / agent path: use provided map_path directly.
    if map_path or not _can_prompt():
        selected = _selected_or_arg_map(map_path) if map_path else None
        if map_path and selected is None:
            return 1
        _write_stdout_lines(_map_browser_lines(selected=selected))
        if selected is not None:
            return _render_minimap_for_map(selected, pause_on_success=pause_on_success)
        if pause_on_success:
            _pause("  Press Enter to continue.")
        return 0

    # FL-4371: in audit mode the renderer is inactive so the interactive
    # browse path would be invisible.  Fall back to a stdout listing that
    # prints the map list and prompts via cooked-mode _prompt_char, keeping
    # the same Enter-to-view / q-to-cancel semantics.
    if _AUDIT_MODE:
        maps = _available_a3d_maps()
        if not maps:
            console.print("  [yellow]![/yellow]  No .a3d maps found under assets/a3d.")
            return 1
        _write_stdout_lines(_map_browser_lines())
        console.print()
        console.print("  Enter map # to view, or q to cancel.")
        raw = _prompt_char("  > ")
        if raw == "q" or not raw:
            return 0
        try:
            idx = int(raw) - 1
            if idx < 0 or idx >= len(maps):
                console.print(f"  [red]x[/red]  Invalid choice: {raw!r}")
                _pause("  Press Enter to continue.")
                return 0
            chosen = maps[idx]
        except ValueError:
            console.print(f"  [red]x[/red]  Invalid choice: {raw!r}")
            _pause("  Press Enter to continue.")
            return 0
        _set_selected_map(chosen)
        return _render_minimap_for_map(chosen, pause_on_success=True)

    # ── Interactive path ───────────────────────────────────────────────────────
    maps = _available_a3d_maps()
    if not maps:
        console.print("  [yellow]![/yellow]  No .a3d maps found under assets/a3d.")
        return 1

    default_res = _SELECTED_MAP_PATH.resolve() if _SELECTED_MAP_PATH else None
    CLI_ANYTHING_ROOT = REPO_ROOT / "docs/agent/cli-anything"
    MINIMAP_PY = CLI_ANYTHING_ROOT / "cli_anything/asciiid/core/minimap.py"

    def _label_for_map(p: Path) -> str:
        tags: list[str] = []
        if p == DEFAULT_MAP.resolve():
            tags.append("default")
        if default_res is not None and p == default_res:
            tags.append("selected")
        suffix = f"  [{' '.join(tags)}]" if tags else ""
        return _repo_relative(p) + suffix

    # FL-3482: inline minimap live preview — render for the cursor item on every tick
    _mm_preview_cache: dict[str, str] = {}

    def _minimap_preview_for(map_p: Path) -> str:
        """Return cached minimap ANSI grid string for *map_p*."""
        key = str(map_p)
        if key in _mm_preview_cache:
            return _mm_preview_cache[key]
        try:
            import importlib.util as _ilib_util
            import sys as _sys
            _cp = str(CLI_ANYTHING_ROOT)
            if _cp not in _sys.path:
                _sys.path.insert(0, _cp)
            spec = _ilib_util.spec_from_file_location("_mm_core", str(MINIMAP_PY))
            mod = _ilib_util.module_from_spec(spec)  # type: ignore[attr-defined]
            spec.loader.exec_module(mod)  # type: ignore[attr-defined]
            output = mod.render_minimap_from_a3d(
                map_path=str(map_p),
                scale=16.0,
                width=32,
                height=16,
                show_meshes=True,
            )
            # Strip the header line ("Minimap [...]") so only the grid is shown
            lines = output.split("\n")
            if lines and "Minimap" in lines[0]:
                lines = lines[1:]
            _mm_preview_cache[key] = "\n".join(lines)
        except Exception:
            _mm_preview_cache[key] = "[minimap unavailable]"
        return _mm_preview_cache[key]

    # Pre-render previews in a background thread so the UI opens immediately.
    import threading as _threading
    _bg_thread = _threading.Thread(
        target=lambda: [_minimap_preview_for(mp) for mp in maps],
        daemon=True,
    )
    _bg_thread.start()

    _SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
    _spinner_idx = 0

    # ── Selector state ────────────────────────────────────────────────────────
    _cursor = 0
    _vtop = 0
    _query = ""
    _selected_map: Path | None = None
    _BROWSE_DONE = object()
    _BROWSE_CANCEL = object()

    def _filter_maps(all_maps: list[Path], q: str) -> list[Path]:
        if not q:
            return list(all_maps)
        ql = q.lower()
        return [m for m in all_maps if ql in _repo_relative(m).lower()]

    def _handle_browse_key(
        key: str, fmaps: list[Path]
    ) -> object:
        n = len(fmaps)
        nonlocal _cursor, _vtop, _query, _selected_map
        if key == "up":
            _cursor = max(0, _cursor - 1)
        elif key == "down":
            _cursor = min(max(0, n - 1), _cursor + 1)
        elif key == "pgup":
            _cursor = max(0, _cursor - 10)
        elif key == "pgdn":
            _cursor = min(max(0, n - 1), _cursor + 10)
        elif key == "home":
            _cursor = 0
        elif key == "end":
            _cursor = max(0, n - 1)
        elif key in ("\r", "\n"):
            if fmaps:
                _selected_map = fmaps[_cursor]
                _set_selected_map(_selected_map)
                if action_menu:
                    _map_context_menu(_selected_map)
                    return None  # stay in browse loop
                return _BROWSE_DONE
        elif key in ("\x1b", "q"):
            if _query:
                _query = ""
                _cursor = 0
                _vtop = 0
            else:
                return _BROWSE_CANCEL
        elif key in ("\x7f", "\x08"):  # backspace
            _query = _query[:-1]
            _cursor = 0
            _vtop = 0
        elif len(key) == 1 and key.isprintable():
            _query += key
            _cursor = 0
            _vtop = 0
        return None

    while True:
        filtered = _filter_maps(maps, _query)
        _cursor = max(0, min(_cursor, max(0, len(filtered) - 1)))
        view_h = max(1, _renderer.content_height - 7)

        if _cursor < _vtop:
            _vtop = _cursor
        elif _cursor >= _vtop + view_h:
            _vtop = _cursor - view_h + 1

        content_lines: list[str] = []
        # Header
        _enter_hint = "Enter actions" if action_menu else "Enter view"
        content_lines.append(f"  \033[1mSelect map — arrow keys + filter  |  {_enter_hint}  |  q cancel\033[0m")
        content_lines.append(f"  Filter: {_query}\033[7m \033[0m")
        content_lines.append("")

        if not filtered:
            content_lines.append("  \033[2m(no maps match)[/dim]")
            _is_loading = False
        else:
            # Build two-column: [minimap preview | map list]
            # The minimap column is 36 visible chars wide (2 indent + border + 32 cells + border).
            # Do NOT slice preview lines — ANSI escape codes corrupt sliced bytes.
            # Use the full `pl` string (already the correct visual width) and pad
            # empty rows with spaces so the list column stays aligned.
            _MM_COL_W = 38  # 36 visual + 2 separator spaces
            cursor_item = filtered[_cursor]
            cursor_key = str(cursor_item)
            _is_loading = cursor_key not in _mm_preview_cache
            if _is_loading:
                frame = _SPINNER_FRAMES[_spinner_idx % len(_SPINNER_FRAMES)]
                preview_text = f"  {frame} Loading minimap..."
            else:
                preview_text = _mm_preview_cache[cursor_key]
            preview_lines = preview_text.split("\n")

            list_lines: list[str] = []
            end = _vtop + view_h
            for i, mp in enumerate(filtered[_vtop:end], _vtop):
                label = _label_for_map(mp)
                if i == _cursor:
                    list_lines.append(f"  \033[7m▶ {label} \033[0m")
                else:
                    list_lines.append(f"    {label}")

            # Pad preview to same height as list so columns align
            while len(preview_lines) < len(list_lines):
                preview_lines.append("")

            for pl, ll in zip(preview_lines, list_lines):
                if pl:
                    content_lines.append(f"{pl}  {ll}")
                else:
                    content_lines.append(f"{' ' * _MM_COL_W}{ll}")

            # Info bar below the two-column area
            content_lines.append("")
            content_lines.append(
                f"  [dim]\033[2m{_repo_relative(cursor_item)}  "
                f"size={cursor_item.stat().st_size}[/dim]\033[0m"
            )

        count_str = str(len(filtered)) if len(filtered) == len(maps) else f"{len(filtered)}/{len(maps)}"
        _enter_status = "Enter actions" if action_menu else "Enter view"
        status = (
            f"[dim]  [{count_str} maps]  "
            f"↑↓ PgUp/PgDn scroll  type filter  "
            f"{_enter_status}  q cancel[/dim]"
        )

        _renderer.set_content(content_lines)
        _renderer.set_status(status)
        _renderer.render()

        # Animate spinner while current minimap is loading; block otherwise.
        key = _renderer.input_char(valid_keys=None, timeout=0.1 if _is_loading else None)
        if key is None:
            _spinner_idx += 1
            continue
        result = _handle_browse_key(key, filtered)
        if result is _BROWSE_DONE:
            # Enter pressed — render full minimap then return
            if _selected_map is not None:
                return _render_minimap_for_map(_selected_map, pause_on_success=True)
            return 0
        if result is _BROWSE_CANCEL:
            return 0


def _select_map_by_index(raw_choice: str) -> Path | None:
    try:
        selected = int(raw_choice)
    except ValueError:
        return None
    maps = _available_a3d_maps()
    if selected < 1 or selected > len(maps):
        console.print(f"  [red]x[/red]  map number out of range: {selected}")
        return None
    path = _set_selected_map(maps[selected - 1])
    console.print(f"  Selected map: {_repo_relative(path)}")
    return path


def _render_minimap_for_map(map_path: Path, *, pause_on_success: bool = False) -> int:
    console.print(f"  Minimap preview: {_repo_relative(map_path)}", markup=False, highlight=False)
    return _run_command(
        [_repo_python(), "docs/agent/cli-anything/minimap_render.py", "--map", _repo_relative(map_path)],
        label="minimap preview",
        cwd=REPO_ROOT,
        pause_on_success=pause_on_success,
    )


def _swap_map_for_single_player(map_path: str | None = None) -> int:
    selected = _selected_or_arg_map(map_path)
    if selected is None:
        return 1
    output = _write_map_selection("single-player", selected)
    console.print(f"  [green]✓[/green]  Single-player map set to {_repo_relative(selected)}")
    console.print(f"  selection: {_repo_relative(output)}")
    console.print(f"  next launch: {_repo_python()} scripts/launcher.py --action game-single-player")
    return 0


def _swap_map_for_multiplayer(map_path: str | None = None, *, target: str) -> int:
    if target not in {"candidate", "current"}:
        console.print(f"  [red]x[/red]  unsupported multiplayer map target: {target}")
        return 1
    selected = _selected_or_arg_map(map_path)
    if selected is None:
        return 1
    output = _write_map_selection(target, selected)
    console.print(f"  [green]✓[/green]  {target} multiplayer map set to {_repo_relative(selected)}")
    console.print(f"  selection: {_repo_relative(output)}")
    console.print("  server runtime supports --map/--world; deploy/run-watchdog integration still must consume this artifact before remote proof.")
    return 0


def _open_selected_map_in_asciiid(map_path: str | None = None) -> int:
    selected = _selected_or_arg_map(map_path)
    if selected is None:
        return 1
    if not _prove_asciiid_loads_map(selected):
        return 1
    return _run_asciiid_editor("Map Editor", map_path=_repo_relative(selected))


_MAP_ACTION_OPTS: list[tuple[str, str]] = [
    ("s", "Set Local Game Map  — use selected for single player"),
    ("c", "Set Candidate Map  — push to staging server"),
    ("u", "⚠  Set Current (Live) Map  — replaces map for all players"),
    ("o", "Open in ASCIIID  — 3D map editor"),
    ("e", "Edit Instances  — list or delete objects"),
    ("n", "New Blank/Test Map  — generate minimal flat map"),
    ("q", "← Back"),
]


def _map_context_menu(map_path: Path) -> None:
    """Show action context menu for a specific map (called from browse on Enter)."""
    _set_selected_map(map_path)
    map_path_str = str(map_path)
    while True:
        _chosen = _fuzzy_select(
            _MAP_ACTION_OPTS,
            title=f"Map Actions  ·  {_repo_relative(map_path)}",
            label_fn=lambda t: t[1],
            console=console,
            renderer=_renderer,
        )
        choice = _chosen[0] if _chosen is not None else "q"

        if choice == "q":
            return
        if choice == "s":
            _swap_map_for_single_player(map_path_str)
            _pause("  Press Enter to continue.")
            continue
        if choice == "c":
            if _can_prompt():
                confirm = _prompt_choice("  Deploy to candidate staging server. Confirm? [y/N]: ").lower()
                if confirm != "y":
                    console.print("  [dim]Cancelled.[/dim]")
                    continue
            _swap_map_for_multiplayer(map_path_str, target="candidate")
            _pause("  Press Enter to continue.")
            continue
        if choice == "u":
            if _can_prompt():
                confirm = _prompt_choice("  ⚠ Deploy to LIVE production server. Confirm? [y/N]: ").lower()
                if confirm != "y":
                    console.print("  [dim]Cancelled.[/dim]")
                    continue
            _swap_map_for_multiplayer(map_path_str, target="current")
            _pause("  Press Enter to continue.")
            continue
        if choice == "o":
            _open_selected_map_in_asciiid(map_path_str)
            _pause("  Press Enter to continue.")
            continue
        if choice == "e":
            if _can_prompt():
                confirm = _prompt_choice(f"  Edit instances on {map_path.name} (may delete objects). Proceed? [y/N]: ").lower()
                if confirm != "y":
                    console.print("  [dim]Cancelled.[/dim]")
                    continue
            _run_instance_list(map_path_str)
            continue
        if choice == "n":
            picked = _file_picker(ASSET_DIR / "a3d", filter_glob="*.a3d", title="Select output .a3d location") if _can_prompt() else None
            out_path = str(picked) if picked else str(ASSET_DIR / "a3d" / "minimal_1x1.a3d")
            grid = _choose_grid_size(default="1")
            material = _choose_material_id(default="1")
            if grid is None or material is None:
                console.print("  [dim]Cancelled.[/dim]")
                continue
            _run_new_test_map(out_path, str(grid), str(material))
            continue
        console.print(f"  [dim]Unknown key: {choice!r}[/dim]")


def _menu_list_maps() -> None:
    """List maps: browse with inline minimap preview; Enter opens action context menu."""
    _browse_minimap_maps(action_menu=True)


def _export_xp_to_png() -> None:
    """Interactive wrapper around scripts/png2xp2png.py."""
    console.print()
    console.print("  [bold]Export XP → PNG[/bold]  (scripts/png2xp2png.py)")
    console.print("  Enter path to .xp file (relative to repo root, or absolute).")
    console.print("  Examples: assets/sprites/player-nude.xp")
    console.print("            assets/sprites/wolfie-1112.xp")
    console.print()
    if not _can_prompt():
        console.print("  [red]x[/red]  _export_xp_to_png requires interactive TTY.")
        return
    xp_path = _prompt_line("  XP file: ").strip()
    if not xp_path:
        console.print("  [dim]Cancelled.[/dim]")
        return

    xp_full = REPO_ROOT / xp_path if not Path(xp_path).is_absolute() else Path(xp_path)
    if not xp_full.exists():
        console.print(f"  [red]x[/red]  File not found: {xp_full}")
        return

    scale_str = _prompt_line("  Scale (pixels per cell, default 10): ", "10").strip()
    scale = int(scale_str) if scale_str.isdigit() and int(scale_str) >= 1 else 10

    _MODE_OPTS = [
        ("f", "font (default)"),
        ("g", "geo"),
        ("t", "triptych"),
        ("a", "all-layers"),
    ]
    _mode_chosen = _fuzzy_select(
        _MODE_OPTS,
        title="XP export mode",
        label_fn=lambda t: t[1],
        console=console,
        renderer=_renderer,
    )
    mode_str = _mode_chosen[0] if _mode_chosen is not None else "f"
    triptych = mode_str == "t"
    all_layers = mode_str == "a"
    use_font = mode_str != "g"

    out_stem = xp_full.stem + ("_triptych" if triptych else "_layers" if all_layers else "_sheet")
    out_path = Path("/tmp") / f"{out_stem}.png"

    cmd = [_repo_python(), "scripts/png2xp2png.py", str(xp_full), "-o", str(out_path), "--scale", str(scale)]
    if use_font:
        cmd.append("--font")
    if triptych:
        cmd.append("--triptych")
    if all_layers:
        cmd.append("--all-layers")

    console.print(f"  [dim]Running: {' '.join(cmd)}[/dim]")
    _run_command(cmd, label="png2xp2png", cwd=REPO_ROOT)

    if all_layers:
        layer_outputs = sorted(out_path.parent.glob(f"{out_path.stem}_L*.png"))
        if layer_outputs:
            console.print("  Output layers:")
            for layer_path in layer_outputs:
                console.print(f"    {layer_path}")
    elif out_path.exists():
        console.print(f"  Output: {out_path}")
        open_str = console.input("  Open in Preview? [y/N]: ").strip().lower()
        if open_str == "y":
            subprocess.Popen(["open", "-a", "Preview", str(out_path)])
    _pause("  Press Enter to continue.")


def _menu_xp_uv_body_viewer(map_path: "Path | None" = None) -> None:
    """Prompt for a semantic map and launch the UV Body Viewer in anchor-review mode."""
    pipeline_v3 = REPO_ROOT / "pipeline-v3"
    if not pipeline_v3.is_dir():
        pipeline_v3 = REPO_ROOT.parent / "asciicker-pipeline-v3"
    viewer = pipeline_v3 / "scripts" / "xp_uv_body_viewer.py"
    if not viewer.is_file():
        console.print(f"  [red]✗[/red]  UV Body Viewer not found at: {viewer}")
        console.print("  Run: git submodule update --init pipeline-v3")
        _pause("  Press Enter to continue.")
        return
    _draw_submenu_header("UV Body Viewer")
    console.print("  Body map panel auto-shown on left when pipeline-v3/output/<stem>_body_map.xp exists.")
    console.print("  Controls: [b] body map  [a/d] angle  [c] composite  [j/k] cycle skins  [q] quit")
    # Always point at Y9-2 sprites so wolack-attack-*.xp and other authored XPs are found.
    y9_sprites = REPO_ROOT / "assets" / "sprites"
    if map_path is None:
        map_path = _pick_semantic_map(pipeline_v3)
    if map_path is None:
        return
    cmd = [_repo_python(), str(viewer), "--anchor-review", str(map_path),
           "--sprite-dir", str(y9_sprites)]
    if _renderer.active:
        if _io_mgr.submenu_buffering:
            _flush_and_render(f"  Opening UV Body Viewer: {map_path.name}...")
        with _renderer.paused():
            rc = _run_command(cmd, label="UV Body Viewer", cwd=str(pipeline_v3))
    else:
        rc = _run_command(cmd, label="UV Body Viewer", cwd=str(pipeline_v3))
    if rc != 0:
        console.print(f"  [yellow]⚠[/yellow]  UV Body Viewer exited with code {rc}")
    _pause("  Press Enter to continue.")


def _menu_mounted_overlay_validation() -> None:
    """Launch UV Body Viewer pre-selected on wolack-0101.json for G4 rider offset validation."""
    wolack_map = REPO_ROOT / "docs" / "research" / "ascii" / "semantic_maps" / "wolack-0101.json"
    if not wolack_map.is_file():
        console.print(f"  [red]✗[/red]  wolack-0101.json not found at: {wolack_map}")
        console.print("  Expected: docs/research/ascii/semantic_maps/wolack-0101.json")
        _pause("  Press Enter to continue.")
        return
    console.print("  G4 validation: check rider sits on seat_anchor/pelvis contact patch at all 8 angles.")
    console.print("  Press [c] for composite view, [j/k] to cycle rider skins (wolack-attack-*.xp).")
    _menu_xp_uv_body_viewer(map_path=wolack_map)


def _menu_xp_asset_browser() -> None:
    # FL-4144: surface region-grid viewer alongside existing items.
    while True:
        _draw_submenu_header("Sprite Asset Browser")
        _prod_sprites = _xp_animation_candidates(test_fixtures=False)
        _test_sprites = _xp_animation_candidates(test_fixtures=True)
        console.print(f"  sprites: {len(_prod_sprites)} production")
        console.print()
        _menu_line("  [v] View Layer-2 Browser", suffix_markup="[dim]Merged layer-2+ launcher preview[/dim]")
        _menu_line("  [r] Raw Layer Inspector", suffix_markup="[dim]Per-layer XP browser plus semantic dictionary details[/dim]")
        _menu_line("  [u] UV Body Viewer", suffix_markup="[dim]3-panel anchor review + flat body map screen (pipeline-v3)[/dim]")
        _menu_line("  [s] Region Grid Viewer", suffix_markup="[dim]UV Body Viewer pre-loaded on anchor JSONs; press [g] inside viewer[/dim]")
        _menu_line("  [m] Mounted Overlay Validation", suffix_markup="[dim]G4: wolack rider offset check — opens UV Body Viewer pre-loaded on wolack-0101.json[/dim]")
        _menu_line("  [l] Source Layer Contract Viewer", suffix_markup="[dim]FL-4162: read-only — raw layers + reviewed role/topology/blockers + glyph evidence[/dim]")
        _menu_line("  [w] Open XPEdit", suffix_markup="[dim]rikiworld.com/xpedit (web REXPaint-compatible XP editor)[/dim]")
        _menu_line("  [c] Compare Sprite Animations", suffix_markup="[dim]Side-by-side animation comparison (will prompt for slots)[/dim]")
        _menu_line("  [p] Export XP → PNG", suffix_markup="[dim]Cell-accurate PNG render of any .xp sheet (png2xp2png.py)[/dim]")
        _menu_line("  [q] Back")
        choice = _prompt_char("> ")

        if choice == "q":
            return
        if choice == "v":
            _menu_xp_asset_layer2_browser()
            continue
        if choice == "r":
            _menu_xp_raw_layer_inspector()
            continue
        if choice == "u":
            _menu_xp_uv_body_viewer()
            continue
        if choice == "s":
            _menu_region_grid_viewer()
            continue
        if choice == "m":
            _menu_mounted_overlay_validation()
            continue
        if choice == "l":
            _menu_source_layer_contract_viewer()
            continue
        if choice == "w":
            webbrowser.open("https://rikiworld.com/xpedit")
            continue
        if choice == "c":
            _compare_xp_animation_slots()
            _pause("  Press Enter to continue.")
            continue
        if choice == "p":
            _export_xp_to_png()
            continue
        console.print(f"  [dim]Unknown key: {choice!r}[/dim]")


def _menu_region_grid_viewer() -> None:
    """FL-4144: open UV Body Viewer pre-loaded on a per-character anchor JSON.

    The region grid view is built into the UV Body Viewer (key [g] = region
    grid all angles x frames). Anchor files restored from commit 570bae1e5^:
    attack-0001.json, bigbee-0100.json, player-0000.json, player-0010.json,
    player-0100.json, player-1100-anchors.json, player-anchors.json,
    plydie-0000.json, wolack-0101.json, wolfie-0100.json. Inside the viewer
    press [g] to enter region grid mode.

    The *-roles.json and *-spatial.json files in the same directory have
    a DIFFERENT schema (no grid_layout, no reference_xp) and cannot be
    loaded by --anchor-review; they are consumed by pipeline-v3 region
    generators only.
    """
    maps_dir = REPO_ROOT / "docs" / "research" / "ascii" / "semantic_maps"
    # FL-4369: use the content-based anchor-schema filter (same as
    # _pick_semantic_map and _menu_generate_body_map) instead of the old
    # name-based -roles/-spatial exclusion.  The name-based filter missed
    # layer_evidence_cards.manifest.json and upstream_sprite_layer_conventions.json
    # which lack grid_layout/frame_w and crash the viewer.
    candidates, _skipped = _anchor_schema_maps(maps_dir)
    if not candidates:
        console.print(f"  [red]x[/red]  No anchor JSON files in {maps_dir}")
        console.print("  [dim]Restore from commit 570bae1e5^ if missing.[/dim]")
        _pause("  Press Enter to continue.")
        return
    console.print("  Region grid viewer: pick an anchor JSON (press [g] inside viewer for region grid mode)")
    for i, p in enumerate(candidates, 1):
        console.print(f"    [{i}] {p.name}")
    raw = _prompt_choice("  Anchor # (or q to cancel): ").strip()
    if raw == "q" or not raw:
        return
    try:
        idx = int(raw) - 1
        chosen = candidates[idx]
    except (ValueError, IndexError):
        console.print(f"  [red]x[/red]  Invalid choice: {raw!r}")
        _pause("  Press Enter to continue.")
        return
    console.print(f"  Opening UV Body Viewer on: {chosen.name}")
    console.print("  [dim]Inside the viewer: [g] region grid, [r/f] focus region, [c] composite, [q] quit.[/dim]")
    _menu_xp_uv_body_viewer(map_path=chosen)


def _menu_source_layer_contract_viewer() -> None:
    """FL-4162: open the read-only Source Layer Contract Viewer on one XP stem.

    Read-only inspector (not the UV Body Viewer / anchor editor). It joins the
    FL-4162 review artifacts — evidence cards, reviewed decisions, the review
    packet, and the family topology contracts — against the original XP layers and
    shows raw layers, the reviewed role/topology class/blockers, and glyph
    exact/near evidence. It writes nothing and never feeds compiler authority.
    """
    pipeline_v3 = REPO_ROOT / "pipeline-v3"
    if not pipeline_v3.is_dir():
        pipeline_v3 = REPO_ROOT.parent / "asciicker-pipeline-v3"
    viewer = pipeline_v3 / "scripts" / "source_layer_contract_viewer.py"
    if not viewer.is_file():
        console.print(f"  [red]x[/red]  Contract viewer not found at: {viewer}")
        console.print("  Run: git submodule update --init pipeline-v3")
        _pause("  Press Enter to continue.")
        return
    sm = REPO_ROOT / "docs" / "research" / "ascii" / "semantic_maps"
    y9_sprites = REPO_ROOT / "assets" / "sprites"
    # Offer only stems that have reviewed contracts (the viewer fails closed
    # otherwise). Stems come from the topology-contract per_card card_ids.
    stems: list[str] = []
    contracts_path = sm / "family_topology_contracts.json"
    try:
        doc = json.loads(contracts_path.read_text(encoding="utf-8"))
        seen = set()
        for contract in doc.get("contracts", {}).values():
            for pc in contract.get("per_card", []):
                stem = str(pc.get("card_id", "")).rsplit("-L", 1)[0]
                if stem and stem not in seen:
                    seen.add(stem)
                    stems.append(stem)
        stems.sort()
    except (OSError, ValueError):
        stems = []
    if not stems:
        console.print(f"  [red]x[/red]  No reviewed contracts at: {contracts_path}")
        _pause("  Press Enter to continue.")
        return
    _draw_submenu_header("Source Layer Contract Viewer (read-only)")
    console.print("  Controls inside viewer: [ ]/[ ] layer  , . angle  n/p frame  space autoplay  f role-focus  q quit")
    console.print(f"  {len(stems)} reviewed stems (e.g. bigbee-0000, player-0000, wolfie-0001, plydie-0000)")
    raw = _prompt_choice("  XP stem (default bigbee-0000, or q to cancel): ").strip()
    if raw == "q":
        return
    stem = raw or "bigbee-0000"
    if stem not in stems:
        console.print(f"  [red]x[/red]  Stem {stem!r} has no reviewed contract layers.")
        _pause("  Press Enter to continue.")
        return
    cmd = [_repo_python(), str(viewer), stem, "--sprites", str(y9_sprites), "--sm", str(sm)]
    if _renderer.active:
        if _io_mgr.submenu_buffering:
            _flush_and_render(f"  Opening Source Layer Contract Viewer: {stem}...")
        with _renderer.paused():
            rc = _run_command(cmd, label="Source Layer Contract Viewer", cwd=str(pipeline_v3))
    else:
        rc = _run_command(cmd, label="Source Layer Contract Viewer", cwd=str(pipeline_v3))
    if rc != 0:
        console.print(f"  [yellow]⚠[/yellow]  Contract viewer exited with code {rc}")
    _pause("  Press Enter to continue.")


_SCRIPTS_LOCATION_NOTE: dict[str, str] = {
    # testing/ files that belong there (launcher infra)
    "scripts/launcher.py":                   "canonical launcher",
    "testing/_common.py":                    "legacy animation util — not a launcher owner",
    "testing/run_all.py":                    "test runner — consider tests/ or scripts/",
    "testing/test_launcher_ui_renderer.py":  "launcher unit test — consider tests/",
    "testing/test_launcher_ui_scroll_view.py": "launcher unit test — consider tests/",
    # testing/ files that are visual demos, not tests — misplaced
    **{f"testing/anim_{c}_*.py": "visual demo — consider scripts/demos/ or testing/demos/" for c in "ABCDEFGHIJKLMN"},
    "testing/demo_cli_style.py":             "CLI style demo — consider scripts/demos/",
    "testing/anim_A_autosplash.py":          "visual demo — consider scripts/demos/",
    # cli-anything
    "docs/agent/cli-anything/conftest.py":             "pytest conftest — stays in docs/agent/cli-anything/",
    "docs/agent/cli-anything/setup.py":                "harness setup — stays in docs/agent/cli-anything/",
}

_DEMO_PREFIXES = ("anim_", "demo_")


def _script_location_note(rel: str) -> str:
    """Return a relocation note for scripts that appear to be in the wrong place."""
    fname = Path(rel).name
    dirpart = str(Path(rel).parent)
    if dirpart == "testing":
        if fname.startswith(_DEMO_PREFIXES):
            return "⚑ visual demo — consider scripts/demos/"
        if fname.startswith("test_"):
            return "⚑ unit test — consider tests/"
        if fname == "run_all.py":
            return "⚑ test runner — consider tests/ or scripts/"
    return ""


def _list_all_scripts(*, pause_on_success: bool = True) -> None:
    """Live scan of every Python script in scripts/, testing/, docs/agent/cli-anything/ shown as a table."""
    _draw_submenu_header("All Scripts — Live Scan")

    # Directories to scan with their display name
    scan_dirs: list[tuple[Path, str]] = [
        (REPO_ROOT / "scripts",       "scripts/"),
        (REPO_ROOT / "testing",       "testing/"),
        (REPO_ROOT / "docs/agent/cli-anything", "docs/agent/cli-anything/"),
    ]

    tbl = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
    tbl.add_column("File", style="cyan", no_wrap=True)
    tbl.add_column("UX state", style="dim", no_wrap=True)
    tbl.add_column("Note", style="yellow")

    totals: dict[str, int] = {}
    misplaced = 0

    for scan_root, label in scan_dirs:
        if not scan_root.exists():
            continue
        files = sorted(
            p for p in scan_root.glob("*.py")
            if p.is_file() and p.name not in {"__init__.py", "conftest.py"}
        )
        totals[label] = len(files)
        if not files:
            continue
        tbl.add_row(f"[bold]{label}[/bold]", "", "")
        for f in files:
            rel = str(f.relative_to(REPO_ROOT))
            ux = _script_ux_state_for_path(f)
            note = _script_location_note(rel)
            if note:
                misplaced += 1
            tbl.add_row(f"  {f.name}", ux, note)

    console.print(tbl)
    console.print()

    total_all = sum(totals.values())
    console.print(f"  total: {total_all} scripts  " + "  ".join(f"{lbl}{n}" for lbl, n in totals.items()))
    if misplaced:
        console.print(f"  [yellow]⚑ {misplaced} scripts flagged for possible relocation[/yellow]  (⚑ = suggested, not enforced)")
    console.print()
    console.print("  [dim]UX states:  style-compliant = tested CLI  open-user-ux = untriaged  deleted/non-authoritative = do not use[/dim]")

    if pause_on_success:
        _pause("  Press Enter to continue.")


def _menu_dev_tool_scripts() -> None:
    while True:
        _draw_submenu_header("Dev Tool Scripts")
        _menu_line("  [l] All Scripts      live scan", suffix_markup="[dim]Every .py in scripts/ testing/ docs/agent/cli-anything/ with UX state[/dim]")
        _menu_line("  [a] Asset Pipeline   (help)", suffix_markup="[dim]Show pipeline CLI commands and help[/dim]")
        _menu_line("  [b] Blender & OSM  (→ sub-menu)", suffix_markup="[dim]Import/export maps using Blender and OpenStreetMap[/dim]")
        _menu_line("  [c] CLI / CLI Anything  (list)", suffix_markup="[dim]List CLI and agent tools[/dim]")
        _menu_line("  [d] Deployment  (list)", suffix_markup="[dim]Deployment and VPS management scripts[/dim]")
        _menu_line("  [e] Multiplayer / Watchdog  (list)", suffix_markup="[dim]Multiplayer and test scripts[/dim]")
        _menu_line("  [f] Testing & Verification  (list)", suffix_markup="[dim]Test suites, verification, and E2E runners[/dim]")
        _menu_line("  [g] Maintenance  (list)", suffix_markup="[dim]Cleanup, log rotation, and maintenance tasks[/dim]")
        _menu_line("  [h] Sprite Tools  (list)", suffix_markup="[dim]XP sprite sheet conversion, PNG export, cell renderer[/dim]")
        _menu_line("  [m] Glyph Morphology Browser  (TUI)", suffix_markup="[dim]Spike: browse any Unicode glyph by rendered shape + morphology[/dim]")
        _menu_line("  [v] Glyph Families Viewer  (TUI)", suffix_markup="[dim]Browse animated families: spinners, ramps, fill-pairs, cycles[/dim]")
        _menu_line("  [x] Blender & OSM Config", suffix_markup="[dim]Configure Blender and OSM tool paths[/dim]")
        _menu_line("  [q] Back")
        choice = _prompt_char("> ")

        if choice == "q":
            return
        if choice == "l":
            _list_all_scripts(pause_on_success=True)
            continue
        if choice == "m":
            _run_command(
                [_repo_python(), "scripts/glyph_morphology_browser.py"],
                label="Glyph Morphology Browser",
                cwd=REPO_ROOT,
            )
            continue
        if choice == "v":
            _run_command(
                [_repo_python(), "scripts/glyph_families_viewer.py"],
                label="Glyph Families Viewer",
                cwd=REPO_ROOT,
            )
            continue
        if choice == "a":
            _run_command([_repo_python(), "-m", "scripts.pipeline", "--help"], label="pipeline help", cwd=REPO_ROOT)
            continue
        if choice == "b":
            _menu_blender_osm()
            continue
        if choice == "x":
            _run_goto("3.2")
            continue
        family = {
            "c": "cli-anything",
            "d": "deployment",
            "e": "multiplayer-watchdog",
            "f": "testing-verification",
            "g": "maintenance",
            "h": "sprite-tools",
        }.get(choice)
        if family:
            _list_script_family(family, pause_on_success=True)
            continue
        console.print(f"  [dim]Unknown key: {choice!r}[/dim]")


def _show_getting_started() -> None:
    """FL-1885: Getting Started — dependency checklist and project overview for new users."""
    with _capturing_console() as _cap_buf:
        _show_getting_started_content()
    if _cap_buf is not None:
        _ScrollView(_renderer).show_lines(_cap_buf.getvalue().rstrip("\n").split("\n"))
    else:
        _pause("  Press Enter to continue.")


def _show_getting_started_content() -> None:
    console.print()
    console.rule("[bold]Getting Started[/bold]")
    console.print()
    console.print("  [bold]Asciicker[/bold] is an isometric multiplayer game engine with ASCII-art graphics.")
    console.print("  The launcher is your front door to playing, editing maps, and managing servers.")
    console.print()
    console.print("  [bold]Quick start:[/bold]")
    console.print("    1. Run [cyan]make setup[/cyan] to install Python dependencies")
    console.print("    2. Press [cyan][1] GAME → [1] SINGLE PLAYER[/cyan] to play locally")
    console.print("    3. Press [cyan][2] ASSET & MAP EDITOR → [1] Launch ASCIIID Editor[/cyan] to edit maps")
    console.print()
    console.print("  [bold]Dependencies:[/bold]")
    # Check each dependency and show status
    checks = [
        ("Python venv", _health.fast_probes().venv),
        ("Game binary (.run/game)", _health.fast_probes().game),
        ("Server binary (.run/server)", _health.fast_probes().server),
        ("Helper bot chroma index", "ok" if (Path(__file__).resolve().parents[1] / "scripts/launcher_helper_bot/index/chroma" / "chroma.sqlite3").exists() else "warn"),
    ]
    for label, status in checks:
        icon = {"ok": "[green]✓[/green]", "warn": "[yellow]⚠[/yellow]", "fail": "[red]✗[/red]"}.get(status, "[yellow]⚠[/yellow]")
        console.print(f"    {icon} {label}")
    console.print()
    console.print("  [bold]Optional (for advanced features):[/bold]")
    console.print("    • [dim]Emscripten SDK[/dim] — required for web builds ([cyan]source ~/emsdk/emsdk_env.sh[/cyan])")
    console.print("    • [dim]Node.js + Playwright[/dim] — for automated browser tests ([cyan]npx playwright install[/cyan])")
    console.print("    • [dim]Blender 4.x[/dim] — for 3D asset pipeline and OSM terrain import")
    console.print("    • [dim]Helper bot chroma index[/dim] — for AI-assisted repo queries ([cyan]make unpack-chroma[/cyan] or will auto-build on first query)")
    console.print()
    console.print("  [bold]Terminology:[/bold]")
    console.print("    • [cyan]FL / Failure Log[/cyan] — Bug tracker (each FL-NNNN is a tracked issue)")
    console.print("    • [cyan]Watchdog[/cyan] — Automated test system that connects to the server like a player")
    console.print("    • [cyan]VPS[/cyan] — Virtual Private Server (the remote machine running the game)")
    console.print("    • [cyan]Slot[/cyan] — Deployment target: 'candidate' (staging) or 'current' (production)")
    console.print("    • [cyan]Bundle[/cyan] — Compiled sprite/visual asset package deployed to the server")
    console.print("    • [cyan]Triage[/cyan] — Quick diagnosis: what failed, probable cause, next step")
    console.print("    • [cyan]Epoch[/cyan] — Major architecture milestone in the project timeline")
    console.print("    • [cyan]Recorder[/cyan] — Data capture system logging positions, HP, actions during tests")
    console.print()
    console.print("  [dim]Status bar legend: ✓=healthy  ⚠=attention  ✗=broken  🟢=pass  🔴=fail  🟡=warning[/dim]")
    # _pause handled by _show_getting_started via ScrollView or plain pause


def _show_bundle_system_guide() -> None:
    """Plain-English guide to the appearance bundle system (XP → screen chain)."""
    with _capturing_console() as _cap_buf:
        _show_bundle_system_guide_content()
    if _cap_buf is not None:
        _ScrollView(_renderer).show_lines(_cap_buf.getvalue().rstrip("\n").split("\n"))
    else:
        _pause("  Press Enter to continue.")


def _show_bundle_system_guide_content() -> None:
    console.print()
    console.rule("[bold]Bundle System Guide[/bold]")
    console.print()
    console.print("  [bold]Why it exists[/bold]")
    console.print("  Before the bundle system, the client guessed which sprite to draw from")
    console.print("  local skin enums and filename switches. That let client and server diverge")
    console.print("  — the client could show a gold sword the server thought was unarmed.")
    console.print("  Now the server owns visual identity; the client only renders what the")
    console.print("  server authorizes via the compiled bundle.")
    console.print()
    console.print("  [bold]The key terms[/bold]")
    console.print("    [cyan]presentation_kind_id[/cyan]  — what the actor is DOING (idle_walk / attack / death)")
    console.print("                             not an outfit, not a camera angle")
    console.print("    [cyan]skin_definition_id[/cyan]    — which body family (cyan_suit, normal_player)")
    console.print("                             not a body part; the bundle picks the part from the family")
    console.print("    [cyan]variation_id[/cyan]          — server-owned authored variation; 0 is default")
    console.print("    [cyan]rig_id[/cyan]                — server-owned rig seam; 0 is default")
    console.print("    [cyan]item_definition_id[/cyan]    — which equipped item and its render owner")
    console.print("    [cyan]slot_kind_id[/cyan]          — attachment lane (body / head / weapon / mount …)")
    console.print("    [cyan]visual_style_id[/cyan]       — color lane (default / gold / dark), NOT geometry")
    console.print("    [cyan]variant_signature[/cyan]     — geometry tuple (height × width × silhouette)")
    console.print("                             compile-time content only unless promoted to a")
    console.print("                             server-owned key dimension")
    console.print()
    console.print("  [bold]The XP → screen chain[/bold]")
    console.print("    Steps 0–3  [dim]Compiler[/dim]  ActorVisualProfile proof/source corpus deleted")
    console.print("               No launcher command is authoritative for selected-frame proof")
    console.print("    Steps 4–5  [dim]Server[/dim]    server_tick.cpp picks presentation/profile/item/mount")
    console.print("               IDs from gameplay state and sends them to clients")
    console.print("    Step  6    [dim]Client[/dim]    stores those IDs in AppearanceStateV2")
    console.print("    Steps 7–11 [dim]Client[/dim]    resolves an ActorVisualProfile layer stack")
    console.print("               directly from AppearanceStateV2, then composes ordered layers")
    console.print("    Steps 12–15 [dim]Client[/dim]   cache by actor_visual_profile_hash + key, advance frame, draw")
    console.print()
    console.print("  [bold]Common mistakes[/bold]")
    console.print("    [red]✗[/red]  Treating deleted ActorVisualProfile proof files as authority")
    console.print("    [red]✗[/red]  Adding a runtime selector, layer fallback, wrapper, or mounted special case")
    console.print("    [red]✗[/red]  Confusing visual_style_id (color) with variant_signature (geometry)")
    console.print("    [red]✗[/red]  Treating mounted or crossbow rows as special runtime branches")
    console.print("    [red]✗[/red]  Assuming a new skin appears in gameplay just by compiling profiles —")
    console.print("         a server profile or loadout path must also choose the skin_definition_id")
    console.print()
    console.print("  [bold]How to add new art[/bold]")
    console.print("    New skin:     rebuild source-owned visual authoring before claiming proof")
    console.print("    New wearable: rebuild source-owned slot authoring before claiming proof")
    console.print()
    console.print("  [bold]Key commands[/bold]")
    console.print("    [cyan]deleted[/cyan] ActorVisualProfile validate/compile front doors")
    console.print()
    console.print("  [dim]Full master walkthrough: engine/game.cpp top-of-file comment block[/dim]")
    # _pause handled by _show_bundle_system_guide via ScrollView or plain pause


def _menu_info_help() -> None:
    while True:
        _draw_submenu_header("Info / Help")
        _menu_line("  [g] Getting Started", suffix_markup="[dim]New here? Start with this — setup, deps, terminology[/dim]")
        _menu_line("  [b] Bundle System Guide", suffix_markup="[dim]How the visual/sprite system works, XP→screen chain[/dim]")
        _menu_line("  [p] Pipeline CLI Help", suffix_markup="[dim]Asset pipeline CLI reference[/dim]")
        _menu_line("  [w] Workbench Help", suffix_markup="[dim]Browser workbench usage guide[/dim]")
        _menu_line("  [l] Launcher Option Tree", suffix_markup="[dim]Full menu tree as JSON[/dim]")
        _menu_line("  [m] Migration Guide (read-only)", suffix_markup="[dim]Internal migration plan for reference[/dim]")
        _menu_line("  [q] Back")
        choice = _prompt_char("> ")

        if choice == "q":
            return
        if choice == "g":
            _show_getting_started()
            continue
        if choice == "b":
            _show_bundle_system_guide()
            continue
        if choice == "p":
            _run_command([_repo_python(), "-m", "scripts.pipeline", "--help"], label="pipeline help", cwd=REPO_ROOT)
            continue
        if choice == "w":
            _show_workbench_help(pause_on_success=True)
            continue
        if choice == "l":
            import tempfile
            _tree_json = json.dumps(_option_tree.option_tree(), indent=2)
            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", prefix="launcher-option-tree-", delete=False) as _tf:
                _tf.write(_tree_json)
                _tree_path = _tf.name
            console.print(f"  Option tree written to: {_tree_path}")
            console.print(f"  [dim]  open file to inspect ({len(_tree_json)} chars)[/dim]")
            _pause("  Press Enter to continue.")
            continue
        if choice == "m":
            _print_migration_plan(pause_on_success=True)
            continue
        console.print(f"  [dim]Unknown key: {choice!r}[/dim]")


def _menu_asset_map_editor() -> None:
    warned = False
    while True:
        _draw_submenu_header("Asset & Map Editor")
        with _loading("Checking health"):
            bar = _health.fast_probes()
        if not warned:
            _maybe_show_branch_notice("map tools", bar.map_tools, bar.map_detail)
            warned = True
        _menu_line("  [1] Launch ASCIIID Map Editor", suffix_markup="[dim]Open the 3D map editor[/dim]")
        _menu_line("  [2] List Maps", suffix_markup="[dim]Browse and select from available .a3d maps[/dim]")
        _menu_line("  [3] Sprite Asset Browser", suffix_markup="[dim]Browse sprite sheet assets (.xp format)[/dim]")
        _menu_line("  [4] Dev Tool Scripts", suffix_markup="[dim]Browse utility scripts by category[/dim]")
        _menu_line("  [5] Info / Help", suffix_markup="[dim]Getting started, guides, and references[/dim]")
        _menu_line("  [6] Semantic Maps", suffix_markup="[dim]Validate and inspect vendored sprite semantic maps[/dim]")
        _menu_line("  [7] Map Diagnostics", suffix_markup="[dim]Inspect, validate, edit instances, generate test maps[/dim]")
        _menu_line("  [q] Back")
        choice = _prompt_char("> ")

        if choice == "q":
            return
        if choice == "1":
            _menu_asciiid()
        elif choice == "2":
            _menu_list_maps()
        elif choice == "3":
            _menu_xp_asset_browser()
        elif choice == "4":
            _menu_dev_tool_scripts()
        elif choice == "5":
            _menu_info_help()
        elif choice == "6":
            _menu_semantic_maps()
        elif choice == "7":
            _menu_map_diagnostics()
        else:
            console.print(f"  [dim]Unknown key: {choice!r}[/dim]")


def _is_anchor_schema_file(path: "Path") -> bool:
    """True if a semantic-map JSON is an anchor-frame schema loadable by
    ``--anchor-review`` (xp_uv_body_viewer).

    FL-4306: the picker used to offer every ``*.json`` in semantic_maps/,
    including role/spatial defs, the upstream conventions doc, and the
    layer-evidence-card manifest. Those lack ``grid_layout`` and/or have
    ``frame_w <= 0``; opening one crashed the viewer (ValueError at
    xp_uv_body_viewer.py:1290). This is content-based, not name-based, so newly
    added non-anchor JSON is excluded automatically too.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    gl = data.get("grid_layout")
    fw = data.get("frame_w")
    return isinstance(gl, dict) and isinstance(fw, int) and fw > 0


def _anchor_schema_maps(maps_dir: "Path") -> "tuple[list[Path], int]":
    """Return ``(anchor_files, skipped_count)`` for the semantic-map picker.

    Pure (no console/IO side effects) so it is unit-testable. Excludes
    ``schema.json`` and any non-anchor-schema JSON (FL-4306).
    """
    all_json = sorted(p for p in maps_dir.glob("*.json") if p.name != "schema.json")
    anchors = [p for p in all_json if _is_anchor_schema_file(p)]
    return anchors, len(all_json) - len(anchors)


def _pick_semantic_map(pipeline_v3: "Path") -> "Path | None":
    """Fuzzy-select an anchor-schema semantic map JSON; return chosen path or None."""
    maps_dir = REPO_ROOT / "docs" / "research" / "ascii" / "semantic_maps"
    map_files, skipped = _anchor_schema_maps(maps_dir)
    if not map_files:
        console.print(f"  [red]✗[/red]  No anchor-schema semantic maps found in {maps_dir}")
        if skipped:
            console.print(
                f"  [dim]({skipped} non-anchor JSON skipped — roles/spatial/conventions "
                f"lack grid_layout and cannot be anchor-reviewed)[/dim]"
            )
        _pause("  Press Enter to continue.")
        return None
    if skipped:
        console.print(
            f"  [dim]FL-4306: hiding {skipped} non-anchor JSON (no grid_layout)[/dim]"
        )

    def _label(p: "Path") -> str:
        body_map = pipeline_v3 / "output" / f"{p.stem}_body_map.xp"
        suffix = "  ✓ body map" if body_map.is_file() else ""
        return f"{p.name}{suffix}"

    return _fuzzy_select(
        map_files,
        title="Select semantic map",
        label_fn=_label,
        path_fn=lambda p: p,
        console=console,
        renderer=_renderer,
    )


def _menu_generate_body_map() -> None:
    """Prompt for a semantic map JSON and run generate_body_map.py in pipeline-v3."""
    pipeline_v3 = REPO_ROOT / "pipeline-v3"
    if not pipeline_v3.is_dir():
        pipeline_v3 = REPO_ROOT.parent / "asciicker-pipeline-v3"
    generator = pipeline_v3 / "scripts" / "generate_body_map.py"
    if not generator.is_file():
        console.print(f"  [red]✗[/red]  generate_body_map.py not found at: {generator}")
        console.print("  Ensure pipeline-v3 submodule or asciicker-pipeline-v3 sibling is available.")
        _pause("  Press Enter to continue.")
        return
    _draw_submenu_header("Generate Body Map")
    map_path = _pick_semantic_map(pipeline_v3)
    if map_path is None:
        return
    if _renderer.active and _io_mgr.submenu_buffering:
        _flush_and_render(f"  Generating body map for {map_path.name}...")
    _run_command(
        [_repo_python(), str(generator), str(map_path)],
        label="generate body map",
        cwd=str(pipeline_v3),
    )
    _pause("  Press Enter to continue.")


def _menu_semantic_maps() -> None:
    while True:
        _draw_submenu_header("Semantic Maps")
        _menu_line("  [v] Validate", suffix_markup="[dim]Run structural validation against schema[/dim]")
        _menu_line("  [l] List Files", suffix_markup="[dim]Browse vendored semantic map files[/dim]")
        _menu_line("  [g] Generate Body Map", suffix_markup="[dim]Decompose XP sprite into flat body map XP via semantic map (pipeline-v3)[/dim]")
        _menu_line("  [u] UV Body Viewer", suffix_markup="[dim]3-panel anchor review + flat body map screen (pipeline-v3)[/dim]")
        _menu_line("  [q] Back")
        choice = _prompt_char("> ")

        if choice == "q":
            return
        if choice == "v":
            _run_command(
                [_repo_python(), "scripts/validate_semantic_maps.py", "--verbose"],
                label="validate semantic maps",
                cwd=REPO_ROOT,
            )
            continue
        if choice == "l":
            _run_command(["ls", "-la", "docs/research/ascii/semantic_maps/"], label="semantic maps list", cwd=REPO_ROOT)
            continue
        if choice == "g":
            _menu_generate_body_map()
            continue
        if choice == "u":
            _menu_xp_uv_body_viewer()
            continue
        console.print(f"  [dim]Unknown key: {choice!r}[/dim]")


def _menu_map_editor() -> None:
    _menu_asset_map_editor()


def _probe_multiplayer(env: dict[str, str]) -> tuple[_health.ProbeState, _health.ProbeState]:
    _to_probe = {"ok": _health.ProbeState.OK, "fail": _health.ProbeState.FAIL}
    cand = _to_probe.get(_health.mp_probe(env.get("AK_MP_SERVER_CANDIDATE_HOST", "")), _health.ProbeState.UNKNOWN)
    curr = _to_probe.get(_health.mp_probe(env.get("AK_MP_SERVER_CURRENT_HOST", "")), _health.ProbeState.UNKNOWN)
    _health.write_mp_probe_cache(cand, curr)
    return cand, curr


def _require_slot_targets(
    env: dict[str, str],
    slot: str,
    *,
    need_ssh: bool,
    need_runtime: bool,
    action_label: str = "continue",
) -> bool:
    cfg = _load_slot_config(env, slot).as_dict()
    validation_errors = [
        detail
        for detail in (
            str(cfg.get("host_error", "")).strip(),
            str(cfg.get("ssh_user_error", "")).strip(),
        )
        if detail
    ]
    if validation_errors:
        console.print(f"  [red]✗[/red]  {slot.capitalize()} server config is invalid:")
        for detail in validation_errors:
            console.print(f"    - {detail}")
        console.print("  Go to Settings → Multiplayer → Setup Wizard to fix, or edit deploy/server.env manually.")
        _pause("  Press Enter to continue.")
        return False
    missing: list[str] = []
    if need_ssh and not cfg["ssh_target"]:
        missing.append("server address and login (SSH host/user)")
    if need_runtime and not cfg["base_url"]:
        missing.append("base URL")
    if need_runtime and not cfg["ws_server"]:
        missing.append("WS server")
    if not missing:
        # FL-1886: pre-deploy liveness probe — verify target is reachable before proceeding
        host = str(cfg.get("host", ""))
        if host and need_ssh:
            probe_result = _health.mp_probe(host, port=22, timeout=5.0)
            if probe_result == "fail":
                console.print(f"  [red]✗[/red]  {slot} server unreachable — SSH probe to {host}:22 failed.")
                console.print("  The server may be down. Check VPS status before continuing.")
                override = _prompt_choice(f"  server may be down — {action_label} anyway? [y/N]: ").lower()
                if override != "y":
                    console.print(f"  [dim]{action_label.capitalize()} cancelled.[/dim]")
                    return False
                console.print(f"  [yellow]⚠[/yellow]  Proceeding despite unreachable server.")
        return True
    console.print(
        f"  [red]✗[/red]  {slot.capitalize()} server not configured — missing: " + ", ".join(missing) + "."
    )
    console.print("  Go to Settings → Multiplayer → Setup Wizard to configure, or edit deploy/server.env manually.")
    _pause("  Press Enter to continue.")
    return False


def _prompt_recipe_name() -> str | None:
    if not _can_prompt():
        console.print("  [yellow]⚠[/yellow]  Recipe replay selection needs an interactive TTY.")
        console.print("  Use: python3 scripts/launcher.py --action recipe-replay --recipe-name <RECIPE_NAME>")
        return None
    try:
        from scripts.watchdog.recipe_store import list_recipe_names
    except Exception as exc:
        console.print(f"  [red]✗[/red]  Recipes unavailable: {exc}")
        _pause("  Press Enter to continue.")
        return None
    names = list_recipe_names()
    if not names:
        console.print("  [yellow]⚠[/yellow]  No stored recipes found. Create one via Recipes → Make Recipe from Run.")
        _pause("  Press Enter to continue.")
        return None
    return _fuzzy_select(
        names,
        title="Select Recipe",
        label_fn=str,
        console=console,
        renderer=_renderer,
    )


def _menu_recipes(env: dict[str, str]) -> None:
    while True:
        _draw_submenu_header("Recipes")
        _active_recipe = env.get("ACTIVE_RECIPE", "").strip() or "(none)"
        console.print(f"  active recipe: [bold]{_active_recipe}[/bold]" if _active_recipe != "(none)" else "  active recipe: [dim](none)[/dim]")
        try:
            from scripts.watchdog.recipe_store import list_recipe_names
            _recipe_names = list_recipe_names()
            if _recipe_names:
                console.print(f"  available: {len(_recipe_names)} — {', '.join(_recipe_names[:5])}" + (" ..." if len(_recipe_names) > 5 else ""))
            else:
                console.print("  available: [dim](none — create one via Make Recipe from Run)[/dim]")
        except Exception:
            pass
        console.print("  quick path: manual source run -> auto derived repeat")
        console.print("  explicit path: manual test run -> make recipe -> repeat test run")
        # Endpoint health banner — see _DELETED_SCRIPTS.
        if not (REPO_ROOT / "scripts" / "watchdog" / "recipe_store.py").is_file():
            console.print()
            console.print("  [bold red]! watchdog/recipe_store.py is missing[/bold red]")
            console.print("  [dim]All list/show/validate/repeat/export options below will fail visibly when invoked.[/dim]")
        console.print()
        _menu_line("  [h] Help / Workflow", suffix_markup="[dim]Recipe system usage guide[/dim]")
        _menu_line("  [l] List recipes", suffix_markup="[dim]Show all saved test recipes[/dim]")
        _menu_line("  [s] Show recipe", suffix_markup="[dim]Pick a recipe to inspect (or type a name directly here)[/dim]")
        _menu_line("  [m] Make recipe from run", suffix_markup="[dim]Capture a run as a reusable recipe[/dim]")
        _menu_line("  [v] Validate recipe", suffix_markup="[dim]Check a recipe file for errors[/dim]")
        _menu_line("  [d] Dry-run replay command", suffix_markup="[dim]Preview what would run, without executing[/dim]")
        _menu_line("  [p] Repeat recipe", suffix_markup="[dim]Replay a saved recipe against active VPS slot[/dim]")
        _menu_line("  [u] Manual source + auto repeat", suffix_markup="[dim]Run manual control once, then auto-launch derived repeat[/dim]")
        _menu_line("  [a] Auto repeat latest", suffix_markup="[dim]Re-run the most recent recipe[/dim]")
        _menu_line("  [x] Export recipe JSON", suffix_markup="[dim]Save recipe to JSON for sharing[/dim]")
        _menu_line("  [q] Back")
        choice = _prompt_char("> ")

        if choice == "q":
            return
        if choice == "h":
            _recipe_help(pause_on_success=True)
            continue
        if choice == "l":
            _run_command(
                [_repo_python(), "scripts/watchdog/recipe_store.py", "list"],
                label="recipe list",
                cwd=REPO_ROOT,
                pause_on_success=True,
            )
            continue
        if choice == "s":
            recipe_name = _prompt_recipe_name()
            if not recipe_name:
                continue
            _run_command(
                [_repo_python(), "scripts/watchdog/recipe_store.py", "show", recipe_name],
                label="show recipe",
                cwd=REPO_ROOT,
                pause_on_success=True,
            )
            continue
        if choice == "m":
            default_run = _latest_run_id_shared(RUNS_ROOT) or ""
            run_id = _choose_run_id(default_run) or ""
            recipe_name = _prompt_recipe_name() if _can_prompt() else None
            if recipe_name is None and _can_prompt():
                continue
            if recipe_name is None:
                recipe_name = _prompt_line("  Recipe name (e.g. smoke-test-basic)", "")
            if not run_id or not recipe_name:
                console.print("  [red]✗[/red]  make recipe requires a run ID and recipe name.")
                if not _can_prompt():
                    _print_copyable_command(
                        [
                            _repo_python(),
                            "scripts/watchdog/recipe_store.py",
                            "capture-from-run",
                            "<RUN_ID>",
                            "--recipe-name",
                            "<RECIPE_NAME>",
                        ],
                        cwd=REPO_ROOT,
                    )
                _pause("  Press Enter to continue.")
                continue
            if _can_prompt():
                _tab_chosen = _fuzzy_select(
                    ["both", "1", "2"],
                    title="Capture tab",
                    label_fn=str,
                    console=console,
                    renderer=_renderer,
                )
                tab = _tab_chosen if _tab_chosen is not None else "both"
            else:
                tab = "both"
            _run_command(
                _recipe_capture_command(run_id, recipe_name, tab=tab),
                label="make recipe",
                cwd=REPO_ROOT,
                pause_on_success=True,
            )
            continue
        if choice == "v":
            recipe_name = _prompt_recipe_name()
            if recipe_name:
                _validate_recipe_payload(recipe_name)
                _pause("  Press Enter to continue.")
            continue
        if choice == "d":
            recipe_name = _prompt_recipe_name()
            if recipe_name:
                if _validate_recipe_payload(recipe_name) != 0:
                    _pause("  Press Enter to continue.")
                    continue
                _print_copyable_command(_recipe_dry_run_command(recipe_name), cwd=REPO_ROOT)
                _pause("  Press Enter to continue.")
            continue
        if choice == "p":
            recipe_name = _prompt_recipe_name()
            if not recipe_name:
                continue
            if _validate_recipe_payload(recipe_name) != 0:
                _pause("  Press Enter to continue.")
                continue
            diff_corpus = "gameplay"
            if _can_prompt():
                _CORPUS_OPTS = [
                    ("gameplay", "gameplay (default)"),
                    ("watchdog", "watchdog"),
                    ("launcher", "launcher"),
                    ("all", "all"),
                ]
                _corpus_chosen = _fuzzy_select(
                    _CORPUS_OPTS,
                    title="Diff corpus",
                    label_fn=lambda t: t[1],
                    console=console,
                    renderer=_renderer,
                )
                diff_corpus = _corpus_chosen[0] if _corpus_chosen is not None else "gameplay"
            if _can_prompt():
                _play_confirm = _prompt_choice("  Run recipe against active VPS slot? [y/N]: ").lower()
                if _play_confirm != "y":
                    console.print("  [dim]Cancelled.[/dim]")
                    _pause("  Press Enter to continue.")
                    continue
            if not _require_slot_targets(env, "candidate", need_ssh=True, need_runtime=True):
                continue
            previous_run_id = _latest_run_id_shared(RUNS_ROOT)
            rc = _run_command(
                _recipe_repeat_command(
                    env,
                    recipe_name,
                    mode="watchdog-only",
                    hold_open_ms=120000,
                    diff_corpus=diff_corpus,
                ),
                label=f"replay recipe {recipe_name}",
                cwd=REPO_ROOT,
                pause_on_success=True,
            )
            if rc == 0:
                _print_recipe_repeat_summary(recipe_name, previous_run_id=previous_run_id)
                _pause("  Press Enter to continue.")
            continue
        if choice == "u":
            if not _can_prompt():
                console.print("  [yellow]⚠[/yellow]  Manual source + auto repeat requires an interactive TTY.")
                _print_copyable_command(
                    [_repo_python(), "scripts/watchdog_runner.py", "--mode", "watchdog-only", "--target", "candidate", "--controller-mode", "manual", "--followup-repeat-with-derived-recipe"],
                    cwd=REPO_ROOT,
                )
                _pause("  Press Enter to continue.")
                continue
            if not _require_slot_targets(env, "candidate", need_ssh=True, need_runtime=True):
                continue
            _followup_confirm = _prompt_choice(
                "  Run manual controller source on candidate, then auto-launch derived repeat? [y/N]: "
            ).lower()
            if _followup_confirm != "y":
                console.print("  [dim]Cancelled.[/dim]")
                _pause("  Press Enter to continue.")
                continue
            previous_run_id = _latest_run_id_shared(RUNS_ROOT)
            _run_watchdog_with_drillins(
                _recipe_followup_front_door_command(env),
                label="manual source + derived repeat",
                previous_run_id=previous_run_id,
            )
            continue
        if choice == "a":
            _run_command(
                [_repo_python(), "scripts/watchdog_runner.py", "--auto"],
                label="auto repeat latest recipe",
                cwd=REPO_ROOT,
                pause_on_success=True,
            )
            continue
        if choice == "x":
            recipe_name = _prompt_recipe_name()
            if recipe_name:
                _print_copyable_command(
                    [_repo_python(), "scripts/watchdog/recipe_store.py", "show", recipe_name],
                    cwd=REPO_ROOT,
                )
                _pause("  Press Enter to continue.")
            continue
        console.print(f"  [dim]Unknown key: {choice!r}[/dim]")


def _menu_multiplayer_join() -> None:
    while True:
        _draw_submenu_header("Join")
        # FL-1356: show last configured URL as a hint
        _last_url = _default_join_server_url()
        _url_hint = f"  [dim](last: {_last_url})[/dim]" if _last_url else "  [dim](none configured)[/dim]"
        _menu_line("  [u] Enter Server URL", suffix_markup=_url_hint)
        _menu_line("  [l] Show Local Server Join URLs", suffix_markup="[dim]Display URLs for local/LAN server (requires running)[/dim]")
        _menu_line("  [x] Settings  (→ sub-menu)", suffix_markup="[dim]Multiplayer connection settings[/dim]")  # FL-1355: clarify it opens a sub-menu
        _menu_line("  [q] Back")
        choice = _prompt_char("> ")

        if choice == "q":
            return
        if choice == "x":
            _run_goto("3.1")
            continue
        if choice == "u":
            _join_server_url(None)
            _pause("  Press Enter to continue.")
            continue
        if choice == "l":
            _open_local_lan_server(open_browser=False, pause_on_success=True)
            continue
        console.print(f"  [dim]Unknown key: {choice!r}[/dim]")


def _menu_host_local() -> None:
    while True:
        env = _senv.load()
        _draw_submenu_header("Host Local")
        # FL-1363: show local server status near the top
        _local_pids = _repo_local_server_pids()
        _max_players = _local_host_max_players()
        _svr_status = f"[green]running (pid {_local_pids[0]})[/green]" if _local_pids else "[dim]not running[/dim]"
        console.print(f"  server: {_svr_status}")
        console.print()
        _play_suffix = (
            f"[dim]Start local server and get join URL ({_max_players} players)[/dim]"
            if not _local_pids
            else f"[dim]Server {_svr_status} — get join URL ({_max_players} players)[/dim]"
        )
        _menu_line("  [p] Play With Friends", suffix_markup=_play_suffix)  # FL-1358: inline server state
        _menu_line("  [c] Max Players", suffix_markup=f"[dim]Current local host cap: {_max_players}[/dim]")
        _menu_line("  [b] Build Server Binary", suffix_markup="[dim]Compile the multiplayer server (~60s)[/dim]")  # FL-1362: clarify it compiles the binary
        _watchdog_local_alive = (REPO_ROOT / "scripts" / "multiplayer_visual_watchdog.js").is_file()
        if _watchdog_local_alive:
            _menu_line("  [w] Watchdog Monitor (local)", suffix_markup="[dim]Run automated tests against local server[/dim]")
        else:
            _menu_disabled_line("w", "Watchdog Monitor (local)", "backing script multiplayer_visual_watchdog.js is deleted")
        _menu_line("  [s] ⚠ Stop Server", suffix_markup="[dim]Stop the running local server (disconnects active players)[/dim]")
        _menu_line("  [m] Change Map", suffix_markup="[dim]Choose a different map for the server[/dim]")
        _menu_line("  [x] Settings", suffix_markup="[dim]Multiplayer connection settings[/dim]")  # FL-1192/FL-1219: was [c] Local Config; key matches all other multiplayer menus
        _menu_line("  [q] Back")
        choice = _prompt_char("> ")

        if choice == "q":
            return
        if choice == "p":
            _host_local_play_with_friends(pause_on_success=True)
            continue
        if choice == "c":
            _set_local_host_max_players(pause_on_success=True)
            continue
        if choice == "b":
            _run_command(["make", "-C", str(REPO_ROOT), "server"], label="local server build", cwd=REPO_ROOT)
            continue
        if choice == "w":
            _print_local_watchdog_provenance_warning(position="start")
            _run_command(_local_watchdog_command(env), label="watchdog local mode", cwd=REPO_ROOT)
            _print_local_watchdog_provenance_warning(position="end")
            continue
        if choice == "s":
            if _local_pids and _can_prompt():
                confirm = _prompt_choice(f"  Stop local server (pid {_local_pids[0]})? [y/N]: ").lower()
                if confirm != "y":
                    console.print("  [dim]Cancelled.[/dim]")
                    continue
            _stop_repo_local_servers()
            _pause("  Press Enter to continue.")
            continue
        if choice == "m":
            _run_goto("2.2")
            continue
        if choice == "x":
            _run_goto("3.1")
            continue
        console.print(f"  [dim]Unknown key: {choice!r}[/dim]")


def _menu_multiplayer_host() -> None:
    while True:
        _draw_submenu_header("Host")
        # FL-1359: show candidate host inline; FL-1360: clarify both open sub-menus
        _env = _senv.load()
        _cand = _load_slot_config(_env, "candidate").as_dict()
        _cand_host = _cand["display_host"]
        _menu_line("  [1] Host Local  (→ sub-menu)", suffix_markup="[dim]Start a server on this machine[/dim]")  # FL-1360
        _menu_line("  [2] Host VPS", suffix_markup=f"[dim]({_cand_host})[/dim]  (→ sub-menu)")  # FL-1359 + FL-1360
        _menu_line("  [x] Settings", suffix_markup="[dim]Multiplayer connection settings[/dim]")
        _menu_line("  [q] Back")
        choice = _prompt_char("> ")

        if choice == "q":
            return
        if choice == "x":
            _run_goto("3.1")
            continue
        if choice == "1":
            _menu_host_local()
            continue
        if choice == "2":
            _menu_vps_operations_center()
            continue
        console.print(f"  [dim]Unknown key: {choice!r}[/dim]")


def _menu_vps_header() -> None:
    while True:
        _draw_submenu_header("Server Status")
        with _loading("Probing slot liveness"):
            _show_watchdog_context()
        console.print()
        _menu_line("  [l] Latest run summary", suffix_markup="[dim]Show the most recent test run results[/dim]")
        _menu_line("  [n] Next Action Guidance", suffix_markup="[dim]Structured guidance from latest run[/dim]")  # FL-2792
        _menu_line("  [q] Back")
        choice = _prompt_char("> ")

        if choice == "q":
            return
        if choice == "l":
            _show_watchdog_context(pause_on_success=True)
            continue
        if choice == "n":
            _show_next_action_context(pause_on_success=True)
            continue
        # FL-2792: better invalid-input recovery
        valid_choices = {"q", "l", "n"}
        if choice not in valid_choices:
            console.print(f"  [yellow]⚠[/yellow]  '{choice}' is not a recognized key. Use l, n, or q.")
            continue


def _prompt_run_id(default: str | None = None) -> str | None:
    return _choose_run_id(default)


def _menu_analyze_runs() -> None:
    while True:
        latest = _latest_run_id_shared(RUNS_ROOT)
        _draw_submenu_header("Analyze Runs")
        if latest:
            console.print(f"  latest run: {latest}")
        else:
            # FL-3665: clear empty-state notice so menu items are not silently no-ops.
            console.print("  [yellow]⚠[/yellow]  No watchdog runs found.")
            _menu_line("  [w] Run a test now", suffix_markup="[dim]Launch a full test suite — no runs exist yet[/dim]")
        console.print()
        _menu_line("  [l] List / Search", suffix_markup="[dim]Search and filter past test runs[/dim]")
        _menu_line("  [s] Run Summary", suffix_markup="[dim]Full summary of a specific test run[/dim]")
        _menu_line("  [t] Triage", suffix_markup="[dim]Diagnose why a run failed[/dim]")
        _menu_line("  [r] Run Recordings", suffix_markup="[dim]View captured data from a run[/dim]")  # FL-1370: less cryptic label
        _menu_line("  [m] Metrics", suffix_markup="[dim]FPS, latency, and timing data[/dim]")
        _menu_line("  [g] Server Log", suffix_markup="[dim]Server stdout/stderr log excerpt[/dim]")
        _menu_line("  [n] Server Snapshot", suffix_markup="[dim]Entity/tick state at a point in time[/dim]")
        _menu_line("  [a] Artifacts", suffix_markup="[dim]Screenshots, logs, output files[/dim]")
        _menu_line("  [o] Deploy Slot Info", suffix_markup="[dim]Which server a run targeted[/dim]")  # FL-1371: less cryptic label
        _menu_line("  [p] Phases", suffix_markup="[dim]Timing breakdown of each test phase[/dim]")
        _menu_line("  [e] Epochs Timeline", suffix_markup="[dim]Known failures grouped by development era[/dim]")
        _menu_line("  [u] Epoch Statuses", suffix_markup="[dim]Which development-era bugs are still active vs superseded[/dim]")
        _menu_line("  [x] Which Tool / Cross-refs", suffix_markup="[dim]Guide: which tool answers which question[/dim]")
        _menu_line("  [q] Back")
        choice = _prompt_char("> ")

        if choice == "q":
            return
        if choice == "l":
            _run_command([_repo_python(), "scripts/analyze_runs.py", "list"], label="run list", cwd=REPO_ROOT, pause_on_success=True)
            continue
        if choice == "s":
            run_id = _choose_run_id(latest)
            if run_id:
                _run_command([_repo_python(), "scripts/analyze_runs.py", "show", run_id], label="run summary", cwd=REPO_ROOT, pause_on_success=True)
            continue
        if choice in {"t", "r", "m", "g", "a", "o", "p"}:
            run_id = _choose_run_id(latest)
            if run_id:
                subcommand = {
                    "t": "triage",
                    "r": "recorder",
                    "m": "metrics",
                    "g": "server-log",
                    "a": "artifacts",
                    "o": "slot",
                    "p": "phases",
                }[choice]
                _run_command([_repo_python(), "scripts/analyze_runs.py", subcommand, run_id], label=subcommand, cwd=REPO_ROOT, pause_on_success=True)
            continue
        if choice == "n":
            run_id = _choose_run_id(latest)
            if run_id:
                _SNAP_FLAGS = [
                    ("--at", "--at  (time offset, e.g. 5.0)"),
                    ("--tick", "--tick  (tick number, e.g. 120)"),
                    ("--entity", "--entity  (entity name, e.g. tab1)"),
                ]
                _snap_flag_chosen = _fuzzy_select(
                    _SNAP_FLAGS,
                    title="Snapshot — choose flag",
                    label_fn=lambda t: t[1],
                    console=console,
                    renderer=_renderer,
                )
                if _snap_flag_chosen is not None:
                    _snap_flag = _snap_flag_chosen[0]
                    _snap_defaults = {"--at": "5.0", "--tick": "120", "--entity": "tab1"}
                    _snap_val = _prompt_line(f"  Value for {_snap_flag}", _snap_defaults[_snap_flag])
                    if _snap_val:
                        _run_command(
                            [_repo_python(), "scripts/analyze_runs.py", "server-snapshot", run_id, _snap_flag, _snap_val],
                            label="server-snapshot",
                            cwd=REPO_ROOT,
                            pause_on_success=True,
                        )
            continue
        if choice == "e":
            _run_command([_repo_python(), "scripts/analyze_runs.py", "fl", "epochs"], label="epochs timeline", cwd=REPO_ROOT, pause_on_success=True)
            continue
        if choice == "u":
            _run_command([_repo_python(), "scripts/analyze_runs.py", "fl", "epoch-statuses"], label="epoch statuses", cwd=REPO_ROOT, pause_on_success=True)
            continue
        if choice == "x":
            _run_command([_repo_python(), "scripts/analyze_runs.py", "which-tool"], label="which tool", cwd=REPO_ROOT, pause_on_success=True)
            continue
        if choice == "w":
            # FL-3665: interactive shortcut — jump to full run without navigating the tree.
            env = _senv.load()
            _menu_run_watchdog(env)
            continue
        console.print(f"  [dim]Unknown key: {choice!r}[/dim]")


def _menu_failure_log() -> None:
    while True:
        _draw_submenu_header("Failure Log")
        log_path = REPO_ROOT / "docs" / "FAILURE_LOG.md"
        count = 0
        latest_id = "-"
        latest_date = "-"
        latest_severity = "-"
        try:
            for line in log_path.read_text(encoding="utf-8").splitlines():
                if not line.startswith("### FL-"):
                    continue
                count += 1
                m_id = re.match(r"^### (FL-\d+):", line)
                if m_id:
                    latest_id = m_id.group(1)
                m_date = re.search(r"\((\d{4}-\d{2}-\d{2})\)\s*$", line)
                if m_date:
                    latest_date = m_date.group(1)
                m_sev = re.search(r"\[(CRITICAL|HIGH|MEDIUM|LOW)\]", line)
                if m_sev:
                    latest_severity = m_sev.group(1)
        except OSError:
            count = 0
        console.print(f"  entries: {count} · last: {latest_id} {latest_date} · severity: {latest_severity}")
        console.print("  scope: candidate/current VPS failures")
        console.print()
        _menu_line("  [p] Show Log Location", suffix_markup=_dim_suffix("Print the path to the bug tracker file"))
        _menu_line("  [s] Search / Browse Failure Log", suffix_markup=_dim_suffix("Fuzzy-search tracked issues with arrow keys"))  # FL-2282/FL-3481/FL-3571
        _menu_line("  [a] Audit Failure Log  (~10s)", suffix_markup=_dim_suffix("Run consistency checks on the log"))  # FL-1377: clearer label with time hint
        _menu_line("  [f] Show Family Group", suffix_markup=_dim_suffix("All entries in the same family as a given FL ID (use [s] if you don't know the ID)"))
        _menu_line("  [c] View as Card", suffix_markup=_dim_suffix("Show a single failure in card format (use [s] if you don't know the ID)"))
        _menu_line("  [e] Epochs Timeline", suffix_markup=_dim_suffix("Known failures grouped by development era"))
        _menu_line("  [u] Epoch Statuses", suffix_markup=_dim_suffix("Which development-era bugs are still active vs superseded"))
        _menu_line("  [q] Back to VPS Ops")
        choice = _prompt_char("> ")

        if choice == "q":
            return
        if choice == "p":
            _run_command([_repo_python(), "scripts/analyze_failure_log.py", "path"], label="failure-log path", cwd=REPO_ROOT, pause_on_success=True)
            continue
        if choice == "s":
            entry = _choose_failure_log_entry(title="Search / Browse Failure Log")
            if entry:
                _run_command(
                    [_repo_python(), "scripts/analyze_failure_log.py", "card", entry[0]],
                    label="failure-log card",
                    cwd=REPO_ROOT,
                )  # scroll_loop() handles interactive reading
            else:
                console.print("  [dim]Cancelled.[/dim]")
            continue
        if choice == "a":
            term = _prompt_line("  Filter keyword (e.g. launcher, deploy, timeout — blank = audit all entries)", "launcher")
            if not term:
                console.print("  [dim]Cancelled.[/dim]")
                continue
            _run_command(
                [_repo_python(), "scripts/analyze_failure_log.py", "audit", term],
                label="failure-log audit",
                cwd=REPO_ROOT,
            )  # scroll_loop() handles interactive reading
            continue
        if choice in {"f", "c"}:
            subcommand = "family" if choice == "f" else "card"
            title = "Select FL Family Seed" if choice == "f" else "Select FL Card"
            entry = _choose_failure_log_entry(title=title)
            if entry:
                _run_command(
                    [_repo_python(), "scripts/analyze_failure_log.py", subcommand, entry[0]],
                    label=f"failure-log {subcommand}",
                    cwd=REPO_ROOT,
                )  # FL-3619: no pause_on_success — scroll_loop() handles interactive reading
            else:
                console.print("  [dim]Cancelled.[/dim]")
            continue
        if choice == "e":
            _run_command(
                [_repo_python(), "scripts/analyze_failure_log.py", "epochs"],
                label="epochs timeline",
                cwd=REPO_ROOT,
            )  # scroll_loop() handles interactive reading
            continue
        if choice == "u":
            _run_command(
                [_repo_python(), "scripts/analyze_failure_log.py", "epoch-statuses"],
                label="epoch statuses",
                cwd=REPO_ROOT,
            )  # scroll_loop() handles interactive reading
            continue
        console.print(f"  [dim]Unknown key: {choice!r}[/dim]")


def _menu_deploy(env: dict[str, str]) -> None:
    while True:
        _draw_submenu_header("Deploy")
        candidate = _load_slot_config(env, "candidate").as_dict()
        current = _load_slot_config(env, "current").as_dict()
        console.print(f"  candidate (staging): {candidate['display_host']}")
        console.print(f"  current (live):      {current['display_host']}")
        console.print()
        # Endpoint health: the three deploy scripts below are hard-deleted.
        # Render them as visibly disabled so operators know they will not run.
        _server_alive = (REPO_ROOT / "scripts" / "deploy_candidate_server.py").is_file()
        _web_alive = (REPO_ROOT / "scripts" / "deploy_candidate_web.py").is_file()
        _current_alive = (REPO_ROOT / "scripts" / "deploy_current_server.py").is_file()
        if _server_alive:
            _menu_line("  [s] Deploy → candidate server", suffix_markup=f"[dim]Push server binary to staging ({candidate['display_host']})[/dim]")
        else:
            _menu_disabled_line("s", "Deploy → candidate server", "backing script deleted — see _DELETED_SCRIPTS")
        if _web_alive:
            _menu_line("  [w] Deploy web → candidate", suffix_markup=f"[dim]Push web files to staging ({candidate['display_host']})[/dim]")
        else:
            _menu_disabled_line("w", "Deploy web → candidate", "backing script deleted — see _DELETED_SCRIPTS")
        if _current_alive:
            _menu_line("  [c] ⚠ Deploy → CURRENT (live) server", suffix_markup=f"[dim]Push to live production ({current['display_host']}) — cannot undo[/dim]")
        else:
            _menu_disabled_line("c", "Deploy → CURRENT (live) server", "backing script deleted — see _DELETED_SCRIPTS")
        _menu_line("  [q] Back")
        choice = _prompt_char("> ")

        if choice == "q":
            return
        if choice == "s":
            if _require_slot_targets(env, "candidate", need_ssh=True, need_runtime=False):
                _run_command([_repo_python(), "scripts/deploy_candidate_server.py", "--ssh-target", str(candidate["ssh_target"])], label="deploy candidate server", cwd=REPO_ROOT)
            continue
        if choice == "w":
            if _require_slot_targets(env, "candidate", need_ssh=True, need_runtime=True):
                _run_command([_repo_python(), "scripts/deploy_candidate_web.py", "--ssh-target", str(candidate["ssh_target"]), "--base-url", str(candidate["base_url"])], label="deploy candidate web", cwd=REPO_ROOT)
            continue
        if choice == "c":
            # FL-1380: confirmation before deploying to live (current) server
            if _can_prompt():
                console.print(f"  [yellow]⚠[/yellow]  Deploy to CURRENT (live) server: {current['display_host']}")
                _confirm = _prompt_choice("  Confirm live deploy? [y/N]: ").lower()
                if _confirm != "y":
                    console.print("  [dim]Cancelled.[/dim]")
                    continue
            if _require_slot_targets(env, "current", need_ssh=True, need_runtime=False):
                _run_command([_repo_python(), "scripts/deploy_current_server.py", "--ssh-target", str(current["ssh_target"])], label="deploy current server", cwd=REPO_ROOT)
            continue
        console.print(f"  [dim]Unknown key: {choice!r}[/dim]")


def _menu_proof_run_builder() -> None:
    """FL-1601: Proof Run Builder — lazy-imported to avoid degrading launcher startup."""
    try:
        from scripts.launcher_lib.proof_run_builder import menu_proof_run_builder
    except ImportError as exc:
        console.print(f"  [red]✗[/red]  Proof Run Builder unavailable: {exc}")
        console.print("  [dim]This feature requires scripts/launcher_lib/proof_run_builder.py[/dim]")
        _pause("  Press Enter to continue.")
        return
    # When the renderer is active, wrap input_fn to flush the buffer and
    # use cooked mode so the proof run builder's line input works correctly.
    def _safe_input_fn(prompt: str = "") -> str:
        if _renderer.active and _io_mgr.submenu_buffering:
            _flush_and_render()
            with _renderer.cooked_input():
                return input(prompt)
        return input(prompt)

    menu_proof_run_builder(
        console=console,
        input_fn=_safe_input_fn,
        prompt_char_fn=_prompt_char,
        run_command_fn=lambda args, label, cwd: _run_command(args, label=label, cwd=cwd),
        draw_header_fn=lambda: _draw_submenu_header("Proof Run Builder"),
    )


def _post_run_drillins(run_id: str, previous_run_id: str | None = None) -> None:
    """Post-run drill-in menu — lets operator inspect the run without copy-pasting ids."""
    while True:
        summary_data = _read_run_summary(run_id)
        false_gates: list[str] = []
        suggested_fl: list[str] = []
        if summary_data:
            raw_false_gates = summary_data.get("false_gates")
            if isinstance(raw_false_gates, list):
                false_gates = [str(gate) for gate in raw_false_gates if str(gate).strip()]
            if false_gates:
                from scripts.launcher_lib import smart_derive as _smart_derive
                suggested_fl, _ = _smart_derive.fetch_fl_ids_for_gates(false_gates)
        console.print()
        console.rule(f"[bold]Run: {run_id}[/bold]")
        # FL-1889: one-line outcome hint so users know what happened before choosing
        console.print("  [dim]Run complete. Pick an option to inspect results, or [q] to go back.[/dim]")
        console.print()
        # Quick options — useful for any user
        _menu_line("  [s] Show run summary", suffix_markup="[dim]Full verdict, gates, timing[/dim]")
        _menu_line("  [t] Triage", suffix_markup="[dim]Diagnose what went wrong[/dim]")
        if previous_run_id:
            _menu_line(f"  [d] Diff vs {previous_run_id}", suffix_markup="[dim]Compare with previous run[/dim]")
        _menu_line("  [q] Back")
        # Advanced options — for experienced operators
        console.print("  [dim]── advanced ──[/dim]")
        _menu_line("  [r] Recorder fields", suffix_markup="[dim]Captured data points (positions, HP, actions)[/dim]")
        _menu_line("  [m] Metrics", suffix_markup="[dim]FPS, latency, and timing data[/dim]")
        _menu_line("  [x] Lag trace", suffix_markup="[dim]Physics step timing + next-action owner (FL-3600)[/dim]")
        _menu_line("  [g] Server log", suffix_markup="[dim]Server stdout/stderr during the run[/dim]")
        _menu_line("  [n] Server snapshot", suffix_markup="[dim]Entity/tick state at a point in time[/dim]")
        if suggested_fl:
            _menu_line("  [f] Related Bug Entries", suffix_markup="[dim]Bugs from the tracker linked to this run's failure[/dim]")
        else:
            _menu_disabled_line("f", "Related Bug Entries", "run [s] first to find a bug ID")
        _menu_line("  [v] Paste another command", suffix_markup="[dim]Paste a test command to execute[/dim]")
        choice = _prompt_char("> ")

        if choice == "q":
            return
        if choice == "s":
            _run_command([_repo_python(), "scripts/analyze_runs.py", "show", run_id], label="show", cwd=REPO_ROOT)
        elif choice == "r":
            _run_command([_repo_python(), "scripts/analyze_runs.py", "recorder", run_id, "--fields"], label="recorder fields", cwd=REPO_ROOT)
        elif choice == "m":
            _run_command([_repo_python(), "scripts/analyze_runs.py", "metrics", run_id], label="metrics", cwd=REPO_ROOT)
        elif choice == "x":
            # FL-3600: physics step trace + inline next-action owner
            _run_command(
                [_repo_python(), "scripts/analyze_runs.py", "metrics", "--lag", run_id],
                label="lag trace",
                cwd=REPO_ROOT,
            )
            # Inline next-action block from run summary already loaded above
            if summary_data:
                next_action = summary_data.get("next_action")
                op_help = summary_data.get("operator_help") if isinstance(summary_data.get("operator_help"), dict) else {}
                console.print()
                console.rule("[bold]Next Action[/bold]")
                if next_action:
                    console.print(f"  {next_action}")
                else:
                    console.print("  [dim](no next_action in run summary)[/dim]")
                focus = op_help.get("focus_gate") if op_help else None
                if focus:
                    console.print(f"  Focus gate: [bold]{focus.get('gate', '?')}[/bold] — {focus.get('description', '')}")
                actionable = (op_help.get("false_gate_groups") or {}).get("actionable", []) if op_help else []
                if actionable:
                    console.print(f"  Actionable gates ({len(actionable)}):")
                    for _g in actionable[:5]:
                        _fl = _g.get("fl") or ""
                        console.print(f"    {_g['gate']}{' (' + _fl + ')' if _fl else ''}")
                    if len(actionable) > 5:
                        console.print(f"    ... +{len(actionable) - 5} more")
                _pause("  Press Enter to continue.")
        elif choice == "t":
            _run_command([_repo_python(), "scripts/analyze_runs.py", "triage", run_id], label="triage", cwd=REPO_ROOT)
        elif choice == "g":
            _run_command([_repo_python(), "scripts/analyze_runs.py", "server-log", run_id], label="server-log", cwd=REPO_ROOT)
        elif choice == "n":
            _snap2_chosen = _fuzzy_select(
                [("--at", "--at  (time offset, e.g. 5.0)"), ("--tick", "--tick  (tick number, e.g. 120)")],
                title="Snapshot — choose flag",
                label_fn=lambda t: t[1],
                console=console,
                renderer=_renderer,
            )
            if _snap2_chosen is not None:
                _snap2_flag = _snap2_chosen[0]
                _snap2_val = _prompt_line(f"  Value for {_snap2_flag}", "5.0" if _snap2_flag == "--at" else "120")
                if _snap2_val:
                    _run_command([_repo_python(), "scripts/analyze_runs.py", "server-snapshot", run_id, _snap2_flag, _snap2_val], label="server-snapshot", cwd=REPO_ROOT)
        elif choice == "f":
            if not suggested_fl:
                console.print("  [dim](no bug ID derived from this run yet — use [s] to inspect the summary first)[/dim]")
                continue
            console.print(f"  Gates failed: {', '.join(false_gates[:5])}")
            console.print(f"  Related FL entries: {', '.join(suggested_fl[:5])}")
            fl_id = _choose_fl_id(suggested=suggested_fl)
            if fl_id:
                _run_command([_repo_python(), "scripts/analyze_runs.py", "fl", "family", fl_id], label="fl family", cwd=REPO_ROOT)
        elif choice == "d" and previous_run_id:
            _run_command([_repo_python(), "scripts/analyze_runs.py", "diff", previous_run_id, run_id], label="diff", cwd=REPO_ROOT)
        elif choice == "v":
            _paste_and_run_watchdog(_senv.load())
            return
        else:
            console.print(f"  [dim]Unknown key: {choice!r}[/dim]")


def _run_watchdog_with_drillins(
    cmd: list[str],
    label: str,
    previous_run_id: str | None = None,
    *,
    env: dict[str, str] | None = None,
) -> None:
    """Run a watchdog command, then offer the post-run drill-in menu."""
    _run_command(cmd, label=label, cwd=REPO_ROOT, env=env)
    # Try to find the new run id
    new_run_id = _latest_run_id_shared(RUNS_ROOT)
    if new_run_id and new_run_id != previous_run_id:
        _post_run_drillins(new_run_id, previous_run_id=previous_run_id)
    else:
        _pause("  Press Enter to continue.")


def _paste_and_run_watchdog(env: dict[str, str] | None = None) -> None:
    """Paste a broken watchdog command, heal line-break artifacts, and run it.

    Integrates scripts/wrun.py logic: reads multiline pasted text, uses
    --help flag knowledge to heal line-break damage, strips the python3
    prefix, previews the healed command, and execs.
    """
    console.print("  Paste watchdog command, then press [bold]Ctrl-D[/bold] to submit:")
    console.print("  [dim]Line breaks from terminal copy-paste will be auto-healed.[/dim]")
    # Flush buffer and use cooked input for multi-line paste
    if _renderer.active and _io_mgr.submenu_buffering:
        _flush_and_render()
    lines: list[str] = []
    _cooked = _renderer.cooked_input() if _renderer.active else _contextlib.nullcontext()
    try:
        with _cooked:
            while True:
                lines.append(input())
    except EOFError:
        pass
    except KeyboardInterrupt:
        console.print("  [dim]Cancelled.[/dim]")
        return

    raw = "\n".join(lines).strip()
    if not raw:
        console.print("  [red]✗[/red]  Empty input — nothing to run.")
        _pause("  Press Enter to continue.")
        return

    # Import wrun flatten logic
    wrun_path = REPO_ROOT / "scripts" / "wrun.py"
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("wrun", wrun_path)
        assert spec is not None and spec.loader is not None
        wrun_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(wrun_mod)
        flattened, _ = wrun_mod.flatten(raw)
    except Exception as exc:
        console.print(f"  [yellow]⚠[/yellow]  Could not load wrun.py: {exc}")
        console.print("  [dim]Falling back to basic whitespace join.[/dim]")
        flattened = " ".join(raw.split())

    try:
        tokens = shlex.split(flattened)
    except ValueError as exc:
        console.print(f"  [red]✗[/red]  Parse error: {exc}")
        console.print(f"  [dim]Healed text: {flattened[:200]}...")
        _pause("  Press Enter to continue.")
        return

    start = 0
    for i, tok in enumerate(tokens):
        if "watchdog_runner" in tok or "watchdog_run_canonical" in tok:
            start = i + 1
            break
    watchdog_args = tokens[start:]
    if not watchdog_args:
        console.print(f"  [red]✗[/red]  No watchdog args found in pasted command.")
        console.print(f"  [dim]Healed: {flattened[:200]}...")
        _pause("  Press Enter to continue.")
        return

    full_cmd = [_repo_python(), str(REPO_ROOT / "scripts" / "watchdog_runner.py")] + watchdog_args
    child_env = None
    _print_copyable_command(full_cmd, cwd=REPO_ROOT, env=child_env)
    console.print()
    confirm = _prompt_char("  Run this command? [y/n] > ")
    if confirm.lower() != "y":
        console.print("  [dim]Cancelled.[/dim]")
        return
    if env is not None:
        target = _watchdog_target_from_command(full_cmd)
        if target and not _require_watchdog_run_target(env, target):
            return
    previous = _latest_run_id_shared(RUNS_ROOT)
    _run_watchdog_with_drillins(
        full_cmd,
        label="paste & run watchdog",
        previous_run_id=previous,
    )


def _show_watchdog_system_guide() -> None:
    """Full inline reference card for the watchdog automated test system."""
    with _capturing_console() as _cap_buf:
        _show_watchdog_system_guide_content()
    if _cap_buf is not None:
        sv = _ScrollView(_renderer)
        sv.show_lines(_cap_buf.getvalue().rstrip("\n").split("\n"))
    else:
        _pause("  Press Enter to continue.")


def _show_watchdog_system_guide_content() -> None:
    """Content builder for the watchdog guide — prints to current ``console``."""
    console.print()
    console.rule("[bold]Watchdog Automated Test System[/bold]")
    console.print()

    # ── Intro ────────────────────────────────────────────────────────────────
    console.print("  [bold]What it is[/bold]")
    console.print(
        "  The watchdog is an automated multiplayer client that connects to the game"
        " server like a real player, replays scripted controller input against a live"
        " browser session, and checks a corpus of 100+ named proof assertions ('gates')"
        " against the captured gameplay evidence. It is not a unit test runner — it"
        " drives a real Playwright browser against a live VPS deployment and records"
        " positions, animation states, HP, item states, and timing windows per tick."
        " Two browser tabs open simultaneously to test two-player scenarios. The"
        " entire lifecycle — preflight → deploy → launch → input replay → artifact"
        " capture → gate analysis → verdict — is owned by a single canonical front door."
    )
    console.print()

    # ── Pipeline chain ───────────────────────────────────────────────────────
    console.rule("[dim]Pipeline chain[/dim]", align="left")
    console.print()
    for line in [
        "  watchdog_runner.py                 ← ALWAYS use this as your entry point",
        "    │",
        "    ├─ simplified_watchdog_preflight.py    git / bundle / manifest checks",
        "    │    └─ writes watchdog_preflight_receipt.json  (30-min TTL)",
        "    │",
        "    ├─ simplified_watchdog_vps_launcher.py  Playwright browser automation",
        "    │    ├─ Tab 1 CDP :9222 ──┐",
        "    │    └─ Tab 2 CDP :9223 ──┤── watchdog_controller.js  (input hand)",
        "    │                          └── watchdog_recipe_runner.py",
        "    │",
        "    └─ analyze_runs.py                     post-run artifact analysis",
        "         └─ artifacts/maintainer/watchdog_runs/<run-id>/",
        "              summary.json               verdict + gate results + receipt provenance",
        "              recording.jsonl            per-tick gameplay evidence",
        "              watchdog_phase_history.jsonl",
        "              metrics.json",
        "              source_snapshot.tar.gz     inspection-only archived source subset for the proved run",
    ]:
        console.print(f"[dim]{escape(line)}[/dim]")
    console.print()

    # ── Core scripts ─────────────────────────────────────────────────────────
    console.rule("[dim]Core scripts[/dim]", align="left")
    console.print()
    _tbl = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    _tbl.add_column("Script", style="cyan", no_wrap=True)
    _tbl.add_column("Role")
    _tbl.add_column("Typical use", style="dim")
    _tbl.add_row(
        "watchdog_runner.py",
        "Canonical front door — Phase 5 (Phase 5 stub, full lifecycle pending)",
        "Proof Run Builder [p] or direct --mode flag",
    )
    _tbl.add_row(
        "analyze_runs.py",
        "Post-run artifact query — gates, triage, recorder, metrics",
        "analyze_runs.py triage <run-id>",
    )
    _tbl.add_row(
        "watchdog_source.py",
        "Query JS watchdog source — gates, constants, FL cross-refs",
        "watchdog_source.py gate GATE_NAME",
    )
    _tbl.add_row(
        "watchdog/recipe_store.py",
        "Manage saved input scripts (recipes)",
        "watchdog/recipe_store.py list",
    )
    _tbl.add_row(
        "watchdog_remote_slot_admin.py",
        "SSH slot admin — verify, promote, emit identity",
        "Used by preflight + Slot Management menu",
    )
    console.print(_tbl)
    console.print()

    # ── Key concepts ─────────────────────────────────────────────────────────
    console.rule("[dim]Key concepts[/dim]", align="left")
    console.print()
    console.print("  [cyan]Slots[/cyan]")
    console.print("    candidate = staging server — accepts full deploy + proof runs")
    console.print("    current   = live server    — smoke tests only, no redeploy")
    console.print()
    console.print("  [cyan]Modes[/cyan]  (--mode flag on watchdog_runner.py)")
    console.print("    full            reset + deploy + preflight + browser proof  (longest)")
    console.print("    watchdog-only   preflight + browser proof, no redeploy")
    console.print("    current-smoke   bootstrap-only health check on live slot  (fastest)")
    console.print("    promote         deploy candidate binary to current  (admin)")
    console.print("    cherry-pick     deploy specific commits to candidate  (admin)")
    console.print("    dirty mixed scope + Reset & Redeploy Candidate = committed HEAD proof in a disposable tmp clone")
    console.print()
    console.print("  [cyan]Gates[/cyan]")
    console.print("    ~100 named boolean assertions checked after each run.")
    console.print("    Examples: tab1_bootstrap_ok, wearable_visible_remote,")
    console.print("    movement_observed, death_observed, respawn_ok.")
    console.print("    All required gates must pass for a PASS verdict.")
    console.print("    Query gate logic: watchdog_source.py gate GATE_NAME")
    console.print()
    console.print("  [cyan]Recipes[/cyan]")
    console.print("    JSON punch cards in scripts/watchdog_recipes/ describing")
    console.print("    human-like input choreography after game launch.")
    console.print("    Step kinds: validate-attach  sleep  send-key  send-sequence")
    console.print("                hold-key-begin  hold-key-end  barrier")
    console.print()
    console.print("  [cyan]Phases[/cyan]")
    console.print("    world_ready → human_input_ready → gameplay_complete → post-run analysis")
    console.print()
    console.print("  [cyan]Preflight receipt[/cyan]")
    console.print("    File written after passing all preflight checks.")
    console.print("    Expires after 30 minutes. Canonical runner requires it before launch.")
    console.print()
    console.print("  [cyan]Proof profiles[/cyan]  (--proof-profile flag)")
    console.print("    full_candidate   all phases: bootstrap + gameplay + wearables + death")
    console.print("    bootstrap_only   stages A+B only, no gameplay")
    console.print("    wearable         wearable-focused proof")
    console.print("    npc_corpse       NPC corpse proof")
    console.print()

    # ── Live recipe list ──────────────────────────────────────────────────────
    console.rule("[dim]Stored recipes (live)[/dim]", align="left")
    console.print()
    try:
        _rr = subprocess.run(
            [_repo_python(), "scripts/watchdog/recipe_store.py", "list"],
            capture_output=True,
            text=True,
            timeout=8,
            cwd=str(REPO_ROOT),
        )
        _recipe_lines = (_rr.stdout or "").strip().splitlines()
        if _recipe_lines:
            for _rl in _recipe_lines:
                console.print(f"  [dim]{escape(_rl)}[/dim]")
        else:
            console.print("  [dim](no recipes stored)[/dim]")
    except Exception as _exc:
        console.print(f"  [yellow]⚠[/yellow]  recipe list unavailable: {_exc}")
    console.print()

    # ── Query routing ─────────────────────────────────────────────────────────
    console.rule("[dim]Which tool for which question[/dim]", align="left")
    console.print()
    _routing = [
        ('"What did a run show / prove?"', "analyze_runs.py show <run-id>"),
        ('"Which FL fields must a rerun capture?"', "analyze_failure_log.py required-fields FL-NNN --json"),
        ('"I need the mounted tuple first"', "analyze_runs.py recorder <run-id> --mounted"),
        ('"I want the FL-linked C++ path"', "analyze_runs.py callgraph --fl FL-NNN"),
        ('"Why did routing fail closed?"', "analyze_runs.py fl code FL-NNN --json"),
        ('"What does a gate compute?"', "watchdog_source.py gate NAME"),
        ('"Full routing index?"', "analyze_runs.py which-tool"),
    ]
    for _q, _a in _routing:
        console.print(f"  [dim]{_q:<42}[/dim] → [cyan]{_a}[/cyan]")
    console.print()

    # ── Inline --help (trimmed) ───────────────────────────────────────────────
    console.rule("[dim]watchdog_runner.py --help[/dim]", align="left")
    console.print()
    try:
        _hr = subprocess.run(
            [_repo_python(), "scripts/watchdog_runner.py", "--help"],
            capture_output=True,
            text=True,
            timeout=12,
            cwd=str(REPO_ROOT),
        )
        _help_lines = (_hr.stdout or _hr.stderr or "").splitlines()
        # Show first 60 lines; skip the usage line (too wide) but keep options
        _shown = 0
        for _hl in _help_lines:
            if _shown >= 60:
                break
            console.print(f"  [dim]{escape(_hl)}[/dim]")
            _shown += 1
        if len(_help_lines) > 60:
            console.print(f"  [dim]... {len(_help_lines) - 60} more lines — run the script directly for full output[/dim]")
    except Exception as _exc:
        console.print(f"  [yellow]⚠[/yellow]  --help unavailable: {_exc}")
    console.print()
    # _pause handled by caller (_show_watchdog_system_guide) via ScrollView or plain pause


def _vps_liveness_hint(env: dict[str, str]) -> str:
    """Return a one-line candidate VPS liveness indicator for the Run Automated Tests header."""
    host = str(env.get("AK_MP_SERVER_CANDIDATE_HOST", "")).strip()
    if not host:
        return "  candidate VPS: [dim](not configured)[/dim]"
    status = _health.mp_probe(host, port=22, timeout=3.0)
    if status == "ok":
        return f"  candidate VPS: [green]{host}[/green] — reachable"
    return f"  candidate VPS: [red]{host}[/red] — [red]unreachable ⚠[/red]"


def _watchdog_target_from_summary(summary: dict[str, object] | None) -> str | None:
    if not summary:
        return None
    target = str(
        summary.get("target")
        or summary.get("slot")
        or summary.get("target_slot")
        or ""
    ).strip()
    if target in {"candidate", "current", "local"}:
        return target
    return None


def _watchdog_target_from_command(cmd: list[str]) -> str | None:
    for index, token in enumerate(cmd):
        if token == "--target" and index + 1 < len(cmd):
            target = str(cmd[index + 1]).strip()
            if target in {"candidate", "current", "local"}:
                return target
        if token == "--repeat-exact-run" and index + 1 < len(cmd):
            return _watchdog_target_from_summary(_read_run_summary(str(cmd[index + 1]).strip()))
        if token == "--repeat-exact-last":
            latest_run_id = _latest_run_id_shared(RUNS_ROOT)
            if latest_run_id:
                return _watchdog_target_from_summary(_read_run_summary(latest_run_id))
    return None


def _require_watchdog_run_target(env: dict[str, str], slot: str) -> bool:
    if slot == "local":
        return True
    return _require_slot_targets(env, slot, need_ssh=True, need_runtime=True, action_label="run")


def _menu_run_watchdog(env: dict[str, str]) -> None:
    # FL-2015: merged from _menu_run_ops (was [o] Run Operations); re-run items added as [r]/[s]
    while True:
        latest = _latest_run_id_shared(RUNS_ROOT)
        _draw_submenu_header("Run Automated Tests")
        console.print(f"  latest run: {latest or '(none)'}")
        console.print(_vps_liveness_hint(env))
        # Endpoint health banner: every option below funnels through
        # scripts/watchdog_runner.py is the Phase 5 front door. Options below
        # invoke it; runs will exit 1 with a Phase 5 status message until the
        # Phase 5 modules (cli.py, phase_machine.py, etc.) are implemented.
        if not (REPO_ROOT / "scripts" / "watchdog_runner.py").is_file():
            console.print()
            console.print("  [bold red]! watchdog_runner.py is missing[/bold red]  [dim](Phase 5 front door not created yet)[/dim]")
            console.print("  [dim]Every option below will fail visibly at invocation. Use as a dry-run UI only.[/dim]")
        console.print()
        _menu_line("  [g] ⚠ Reset & Redeploy Candidate", suffix_markup="[dim]Resets/redeploys candidate; mixed local edits prove committed HEAD from tmp clone[/dim]")  # FL-1980 pattern: visible danger + consequence before keypress
        _menu_line("  [p] Proof Run Builder      (intent wizard + profiles)", suffix_markup="[dim]Step-by-step wizard for test runs[/dim]")
        _menu_line("  [v] Paste & Run Command    (fix line breaks, preview, exec)", suffix_markup="[dim]Paste a test command, preview it, then run it[/dim]")
        _menu_line("  [f] Full candidate (all services)", suffix_markup="[dim]Complete test suite on staging (~3-5 min)[/dim]")  # FL-1382: clarify scope
        _menu_line("  [o] Visual tests only (candidate slot)", suffix_markup="[dim]Visual tests only on staging[/dim]")
        _menu_line("  [c] Current smoke", suffix_markup="[dim]Quick health check on live server[/dim]")
        _menu_line("  [l] Run candidate locally", suffix_markup="[dim]Run tests against your local server[/dim]")  # FL-1384: clearer label
        _rerun_hint = f"[dim](id: {latest})[/dim]" if latest else "[dim](none)[/dim]"
        _menu_line("  [r] Re-run Latest", suffix_markup=_rerun_hint)
        _menu_line("  [s] Re-run Selected", suffix_markup="[dim]Choose a past run to repeat[/dim]")
        _menu_line("  [?] Watchdog System Guide", suffix_markup="[dim]Architecture, scripts, pipeline, concepts, --help[/dim]")
        _menu_line("  [q] Back")
        choice = _prompt_char("> ")

        if choice == "q":
            return
        if choice == "?":
            _show_watchdog_system_guide()
            continue
        if choice == "p":
            _menu_proof_run_builder()
            continue
        if choice == "v":
            _paste_and_run_watchdog(env)
            continue
        if choice == "g":
            if _require_watchdog_run_target(env, "candidate"):
                try:
                    preview = _candidate_watchdog_scope_preview()
                except RuntimeError as exc:
                    console.print(f"  [red]✗[/red]  {exc}")
                    _pause("  Press Enter to continue.")
                    continue
                console.print()
                for line in preview["lines"]:
                    console.print(line)
                if not preview.get("can_launch"):
                    _pause("  Press Enter to continue.")
                    continue
                if _can_prompt():
                    confirm = _prompt_choice("  ⚠ This will reset/redeploy the candidate and may fork a disposable tmp clone if local edits span multiple scopes (~10-30 min). Confirm? [y/N]: ").lower()
                    if confirm != "y":
                        console.print("  [dim]Cancelled.[/dim]")
                        continue
                previous = _latest_run_id_shared(RUNS_ROOT)
                _run_watchdog_with_drillins(
                    _commit_reset_candidate_watchdog_command(env),
                    label="Reset & Redeploy Candidate",
                    previous_run_id=previous,
                )
            continue
        if choice == "f":
            if _require_watchdog_run_target(env, "candidate"):
                previous = _latest_run_id_shared(RUNS_ROOT)
                _run_watchdog_with_drillins(_watchdog_run_command(env, mode="full", slot="candidate"), label="candidate watchdog (full)", previous_run_id=previous)
            continue
        if choice == "o":
            if _require_watchdog_run_target(env, "candidate"):
                previous = _latest_run_id_shared(RUNS_ROOT)
                _run_watchdog_with_drillins(_watchdog_run_command(env, mode="watchdog-only", slot="candidate"), label="candidate watchdog (watchdog-only)", previous_run_id=previous)
            continue
        if choice == "c":
            if _require_watchdog_run_target(env, "current"):
                previous = _latest_run_id_shared(RUNS_ROOT)
                _run_watchdog_with_drillins(_watchdog_run_command(env, mode="current-smoke", slot="current"), label="current smoke", previous_run_id=previous)
            continue
        if choice == "l":
            _print_local_watchdog_provenance_warning(position="start")
            previous = _latest_run_id_shared(RUNS_ROOT)
            _run_watchdog_with_drillins(_local_watchdog_command(env), label="watchdog local mode", previous_run_id=previous)
            _print_local_watchdog_provenance_warning(position="end")
            continue
        if choice in {"r", "s"}:
            run_id = latest if choice == "r" else _prompt_run_id(latest)
            if not run_id:
                console.print("  [red]✗[/red]  No watchdog run found. Execute a test run first (Full candidate).")
                _pause("  Press Enter to continue.")
                continue
            rerun_summary = _read_run_summary(run_id) or {}
            rerun = _summary_rerun_command(rerun_summary)
            if rerun is None:
                console.print("  [red]✗[/red]  selected summary does not contain mode, target, ssh_target, base_url, and ws_server.")
                _pause("  Press Enter to continue.")
                continue
            _print_copyable_command(rerun, cwd=REPO_ROOT)
            if _can_prompt():
                _exec_confirm = _prompt_choice("  Run this command now? [y/N]: ").lower()
                if _exec_confirm == "y":
                    rerun_target = _watchdog_target_from_summary(rerun_summary) or _watchdog_target_from_command(rerun)
                    if rerun_target and not _require_watchdog_run_target(env, rerun_target):
                        continue
                    previous = _latest_run_id_shared(RUNS_ROOT)
                    _run_watchdog_with_drillins(rerun, label=f"re-run {run_id}", previous_run_id=previous)
                    continue
            _pause("  Press Enter to continue.")
            continue
        console.print(f"  [dim]Unknown key: {choice!r}[/dim]")


def _menu_trust_audit() -> None:
    while True:
        _draw_submenu_header("Legacy Health Check")
        console.print("  legacy canary only; not an R1-R9 proof gate")
        console.print("  [dim]⚠ Options below connect to real VPS[/dim]")  # FL-1391: clarify live impact
        console.print()
        _trust_alive = (REPO_ROOT / "scripts" / "watchdog_trust_audit.py").is_file()
        if _trust_alive:
            _menu_line("  [r] Run legacy strict audit", suffix_markup="[dim]Run old audit script (non-authoritative)[/dim]")
        else:
            _menu_disabled_line("r", "Run legacy strict audit", "backing script deleted — see _DELETED_SCRIPTS")
        _menu_line("  [v] View recent legacy result", suffix_markup="[dim]Show last audit output (non-authoritative)[/dim]")
        _menu_line("  [q] Back")
        choice = _prompt_char("> ")

        if choice == "q":
            return
        if choice == "r":
            _print_legacy_trust_audit_warning()
            _run_command([_repo_python(), "scripts/watchdog_trust_audit.py", "--strict"], label="strict trust audit", cwd=REPO_ROOT, pause_on_success=True)
            continue
        if choice == "v":
            _show_trust_audit_result(pause_on_success=True)
            continue
        console.print(f"  [dim]Unknown key: {choice!r}[/dim]")


# ---------------------------------------------------------------------------
# VPS Operations Center helpers (FL-2792)
# ---------------------------------------------------------------------------


def _game_liveness_ws_probe(ws_server: str, timeout: float = 5.0) -> tuple[str, str]:
    """Probe game-liveness via WS upgrade to /ws/y8/ + framed join-byte payload.

    Opens a raw WebSocket upgrade to the game's canonical WS path (/ws/y8/),
    then sends the join byte 0x6A as a masked binary WebSocket frame (0x82 = FIN + binary opcode, matching WebSocket.send(Uint8Array)).
    If the port is up but no game handler responds, the native server is dead
    or hung behind nginx — classic "nginx green, game dead".

    NOTE: This is a heuristic probe using the canonical game WS path and proper
    WS framing, but it is NOT a full game-protocol proof. The response is read
    as raw bytes from the upgraded socket; a 101 + any response within 2s is
    treated as "alive," but a real player session would require full protocol
    framing negotiation.

    Plain HTTP/manifest probes are secondary; this probe is the closest we
    have to a real native-game check without a real client.

    Returns (status, detail).
    """
    if not ws_server:
        return "skip", "WS server not set"
    if ":" in ws_server:
        host, port_str = ws_server.rsplit(":", 1)
        try:
            port = int(port_str)
        except ValueError:
            return "skip", f"unparseable port in {ws_server}"
    else:
        host = ws_server
        port = 443
    tls = port == 443
    try:
        if tls:
            import ssl as _ssl
            ctx = _ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = _ssl.CERT_NONE
            sock = ctx.wrap_socket(socket.socket(socket.AF_INET, socket.SOCK_STREAM), server_hostname=host)
        else:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))

        # Send minimal WebSocket upgrade request to /ws/y8/ (canonical game WS path)
        key = base64.b64encode(os.urandom(16)).decode()
        upgrade_req = (
            f"GET /ws/y8/ HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"\r\n"
        ).encode()
        sock.sendall(upgrade_req)

        # Read HTTP upgrade response
        resp_raw = b""
        while b"\r\n\r\n" not in resp_raw:
            chunk = sock.recv(4096)
            if not chunk:
                break
            resp_raw += chunk
        header_end = resp_raw.find(b"\r\n\r\n")
        if header_end < 0:
            sock.close()
            return "fail", f"WS upgrade: no HTTP response from {host}:{port}"
        status_line = resp_raw[: resp_raw.index(b"\r\n")].decode(errors="replace")
        if "101" not in status_line:
            sock.close()
            return "fail", f"WS upgrade rejected: {status_line.strip()}"

        # Send the join byte 0x6A as a masked binary WebSocket frame
        # 0x82 = FIN + binary opcode (matches browser WebSocket.send(Uint8Array))
        # 0x81 = MASK=1, payload length=1
        mask_key = os.urandom(4)
        masked_payload = bytes([0x6A ^ mask_key[0]])
        frame = bytes([0x82, 0x81]) + mask_key + masked_payload
        sock.sendall(frame)

        # Wait briefly for any response frame
        sock.settimeout(2.0)
        try:
            resp_frame = sock.recv(4096)
        except socket.timeout:
            sock.close()
            return "warn", f"WS upgraded but no game response (game binary may be dead)"
        sock.close()
        if len(resp_frame) >= 1:
            return "ok", f"WS upgraded + game response ({len(resp_frame)} bytes)"
        return "warn", f"WS upgraded but response too short ({len(resp_frame)} bytes)"
    except Exception as exc:
        return "fail", f"{type(exc).__name__}: {exc}"


def _slot_game_liveness_tiers(cfg: dict[str, str | int]) -> list[tuple[str, str, str]]:
    """Return liveness tiers for a single slot, including real game-liveness.

    FL-2792 fix 2: replaces the old HTTP-only probe with a WebSocket join-byte
    probe that actually tests the game binary is alive, not just nginx-green.
    FL-2792 fix 4: stale _ws_target_parts / _https_manifest_probe deleted;
    logic inlined here directly. Only one canonical liveness helper set remains.
    """
    host = str(cfg.get("host") or "")
    base_url = str(cfg.get("base_url") or "")
    ws_server = str(cfg.get("ws_server") or "")
    tiers = []

    # SSH reachable
    tiers.append(("SSH/22", *_ssh_probe(host)))

    # Real game-liveness: WebSocket join-byte probe (not just HTTP 200)
    tiers.append(("Game WS", *_game_liveness_ws_probe(ws_server)))

    # WebSocket upgrade (host/port/tls derived inline from cfg)
    _domain = str(cfg.get("domain") or "")
    _host = str(cfg.get("host") or "")
    if _domain:
        tiers.append(("WS Upgrade", *_health._probe_ws_upgrade(_domain, 443, tls=True)))
    elif _host:
        try:
            _port = int(cfg.get("port") or DEFAULT_LOCAL_WS_PORT)
        except (TypeError, ValueError):
            _port = DEFAULT_LOCAL_WS_PORT
        tiers.append(("WS Upgrade", *_health._probe_ws_upgrade(_host, _port, tls=False)))
    else:
        tiers.append(("WS Upgrade", "skip", "ws target not set"))

    # HTTPS manifest (bundle identity) — inlined, secondary probe
    if not base_url:
        tiers.append(("Manifest HTTPS", "skip", "base URL not set"))
    else:
        _parsed = urlparse(base_url)
        if _parsed.scheme == "https":
            _man_url = base_url.rstrip("/") + "/slot_manifest.json"
            try:
                _req = Request(_man_url, headers={"User-Agent": "asciicker-testing-launcher/1"})
                with urlopen(_req, timeout=3.0) as _resp:
                    _man_status = getattr(_resp, "status", 0)
                    _resp.read(1)
            except OSError as _exc:
                tiers.append(("Manifest HTTPS", "fail", f"{_man_url} ({_exc})"))
            else:
                if 200 <= int(_man_status) < 300:
                    tiers.append(("Manifest HTTPS", "ok", _man_url))
                else:
                    tiers.append(("Manifest HTTPS", "fail", f"{_man_url} (HTTP {_man_status})"))
        else:
            tiers.append(("Manifest HTTPS", "skip", f"{base_url} is not HTTPS"))

    return tiers


def _ssh_probe(host: str, port: int = 22, timeout: float = 3.0) -> tuple[str, str]:
    """Quick SSH reachability probe (TCP connect to port 22)."""
    if not host:
        return "skip", "host not set"
    status = _health.mp_probe(host, port, timeout=timeout)
    return status, f"{host}:{port}"








def _show_next_action_context(*, pause_on_success: bool = False) -> int:
    """Display the latest run's next_action and operator_help (FL-2792).

    Surfaces the canonical run's structured guidance ("Investigate <gate>",
    "Fix the first blocked gate", etc.) directly in the launcher, so the operator
    does not need to run analyze_runs.py show just to learn what to do next.
    """
    selected_run_id = _latest_run_id_shared(RUNS_ROOT)
    if not selected_run_id:
        console.print("  No watchdog runs found.")
        if pause_on_success:
            _pause("  Press Enter to continue.")
        return 0
    summary = _read_run_summary(selected_run_id)
    if not summary:
        console.print(f"  Run {selected_run_id} exists but summary is unreadable.")
        if pause_on_success:
            _pause("  Press Enter to continue.")
        return 0

    console.print(f"  Latest run: {selected_run_id}")

    next_action = summary.get("next_action")
    if next_action:
        console.print(f"  Next action: {next_action}")
    else:
        console.print("  Next action: (none — run may still be in progress or incomplete)")

    op_help = summary.get("operator_help")
    if op_help and isinstance(op_help, dict):
        focus = op_help.get("focus_gate")
        if focus:
            console.print(f"  Focus gate: {focus.get('gate', '?')} — {focus.get('description', '')}")
        false_groups = op_help.get("false_gate_groups", {})
        actionable = false_groups.get("actionable", [])
        if actionable:
            console.print(f"  False gates ({len(actionable)} actionable):")
            for g in actionable[:3]:
                fl_ref = g.get("fl") or ""
                fl_suffix = f" (FL: {fl_ref})" if fl_ref else ""
                console.print(f"    {g['gate']}{fl_suffix}")
            if len(actionable) > 3:
                console.print(f"    ... +{len(actionable) - 3} more")
        next_cmds = op_help.get("next_commands", [])
        if next_cmds:
            console.print("  Help commands:")
            for cmd in next_cmds[:5]:
                console.print(f"    {cmd}")
            if len(next_cmds) > 5:
                console.print(f"    ... +{len(next_cmds) - 5} more")

    console.print()
    console.print("  Copy full analyze command:")
    _print_copyable_command(
        [_repo_python(), "scripts/analyze_runs.py", "show", selected_run_id],
        cwd=REPO_ROOT,
    )

    if pause_on_success:
        _pause("  Press Enter to continue.")
    return 0


def _bounce_candidate_command(env: dict[str, str]) -> list[str]:
    """SSH restart (bounce) command for the candidate slot server."""
    cfg = _load_slot_config(env, "candidate").as_dict()
    return [
        "ssh", str(cfg["ssh_target"]),
        "sudo systemctl restart asciicker-server.service && echo 'candidate restarted'",
    ]


def _bounce_current_command(env: dict[str, str]) -> list[str]:
    """SSH restart (bounce) command for the current (live) slot server."""
    cfg = _load_slot_config(env, "current").as_dict()
    return [
        "ssh", str(cfg["ssh_target"]),
        "sudo systemctl restart asciicker-server.service && echo 'current restarted'",
    ]


def _menu_vps_operations_center() -> None:
    _vps_context_cached_at: float | None = None
    while True:
        env = _senv.load()
        _draw_submenu_header("VPS Operations Center")
        if (
            _vps_context_cached_at is None
            or (time.time() - _vps_context_cached_at) >= _health.MP_PROBE_CACHE_MAX_AGE_S
        ):
            with _loading("Probing slot liveness"):
                _show_vps_liveness_and_context(env)
            _vps_context_cached_at = time.time()
        else:
            cached_stamp = time.strftime("%H:%M:%S", time.localtime(_vps_context_cached_at))
            cached_age_s = max(0, int(time.time() - _vps_context_cached_at))
            cached_age = "just now" if cached_age_s < 60 else f"{cached_age_s // 60}m ago"
            console.print(f"  [dim](context cached at {cached_stamp} — {cached_age}; press [h] to refresh)[/dim]")
        console.print()
        if _topology_value(env) == "none":
            console.print("  [yellow]⚠[/yellow]  topology not configured; mutating VPS actions will fail closed until Settings are complete.")
            console.print()

        # -- Context-sensitive next-action hint (FL-2792) --
        _show_context_next_action_hint()
        console.print()

        # -- Slot-specific quick-actions: each slot gets liveness + bounce --
        cand_cfg = _load_slot_config(env, "candidate").as_dict()
        curr_cfg = _load_slot_config(env, "current").as_dict()
        console.print(f"  Candidate (staging): [bold]{cand_cfg['display_host']}[/bold]")
        _menu_line("    [c] Bounce Candidate Server", suffix_markup="[dim]systemctl restart (SSH)[/dim]")
        console.print(f"  Current (live):     [bold]{curr_cfg['display_host']}[/bold]")
        _menu_line("    [v] Bounce Current Server", suffix_markup="[dim]systemctl restart (SSH)[/dim]")
        console.print()

        _menu_line("  [h] Server Status", suffix_markup="[dim]Connection info, latest run, and bundle state[/dim]")
        _menu_line("  [n] Next Action / Last Run Guidance", suffix_markup="[dim]Structured guidance from latest canonical run[/dim]")  # FL-2792: surfacing next_action + operator_help
        _menu_line("  [a] Analyze Runs", suffix_markup="[dim]Browse and inspect test run data[/dim]")
        _menu_line("  [w] Run Automated Tests", suffix_markup="[dim]Launch, re-run, and manage automated test runs[/dim]")  # FL-2015: merged Run Ops into here
        _menu_line("  [f] Failure Log", suffix_markup="[dim]Browse tracked bugs and failures[/dim]")
        _menu_line("  [d] Deploy", suffix_markup="[dim]Push code/assets to remote servers[/dim]")
        _menu_line("  [s] Slot Management", suffix_markup="[dim]Manage staging/live server environments[/dim]")
        _menu_line("  [y] Switch Target", suffix_markup="[dim]Change which remote server to operate on[/dim]")
        _menu_line("  [r] Recipes", suffix_markup="[dim]Saved test sequences for repeatable runs[/dim]")
        _menu_line("  [m] Mobile / Playwright", suffix_markup="[dim]Browser-based mobile viewport tests[/dim]")
        _menu_line("  [t] Legacy Health Check", suffix_markup="[dim]Read-only server snapshot (may be stale)[/dim]")
        _menu_line("  [x] Settings", suffix_markup="[dim]Multiplayer connection settings[/dim]")
        _menu_line("  [q] Back")
        console.print()
        console.print("  [dim]↓ scroll if items are cut off on small terminals[/dim]")  # FL-2013: scroll hint
        console.print("  [dim]candidate=staging  current=live  slot=server deployment  parity=bundles match[/dim]")
        choice = _prompt_char("> ")

        if choice == "q":
            return
        if choice == "x":
            _run_goto("3.1")
            continue
        if choice == "h":
            _vps_context_cached_at = None  # force re-probe on next redraw
            _menu_vps_header()
            continue
        if choice == "n":
            _show_next_action_context(pause_on_success=True)
            continue
        if choice == "a":
            _menu_analyze_runs()
            continue
        if choice == "f":
            _menu_failure_log()
            continue
        if choice == "d":
            _menu_deploy(env)
            continue
        if choice == "w":
            _menu_run_watchdog(env)
            continue
        if choice == "s":
            _menu_slot_management(env, _topology_value(env))
            continue
        if choice == "y":
            _switch_target_context(pause_on_success=True)
            continue
        if choice == "r":
            _menu_recipes(env)
            continue
        if choice == "m":
            _menu_mobile_playwright(env)
            continue
        if choice == "t":
            _menu_trust_audit()
            continue
        if choice == "c":
            if not _require_slot_targets(env, "candidate", need_ssh=True, need_runtime=False):
                continue
            if _can_prompt():
                confirm = _prompt_choice("  Bounce (restart) candidate server? [y/N]: ").lower()
                if confirm != "y":
                    console.print("  [dim]Cancelled.[/dim]")
                    continue
            _run_command(
                _bounce_candidate_command(env),
                label="bounce candidate",
                cwd=REPO_ROOT,
            )
            continue
        if choice == "v":
            if not _require_slot_targets(env, "current", need_ssh=True, need_runtime=False):
                continue
            if _can_prompt():
                confirm = _prompt_choice("  Bounce (restart) current (live) server? [y/N]: ").lower()
                if confirm != "y":
                    console.print("  [dim]Cancelled.[/dim]")
                    continue
            _run_command(
                _bounce_current_command(env),
                label="bounce current",
                cwd=REPO_ROOT,
            )
            continue
        # FL-2792: better invalid-input recovery than raw 'Unknown key'
        valid_choices = {"q", "x", "h", "n", "a", "f", "d", "w", "s", "y", "r", "m", "t", "c", "v"}
        if choice not in valid_choices:
            console.print(f"  [yellow]⚠[/yellow]  '{choice}' is not a recognized key from this menu.")
            console.print("  Type one of: h a n f d w s y r m t x c v — or q to go back.")
            continue


def _menu_slot_management(env: dict[str, str], topology: str) -> None:
    while True:
        _draw_submenu_header("Slot Management")
        _cand_host = _load_slot_config(env, "candidate").as_dict()["display_host"]
        _curr_host = _load_slot_config(env, "current").as_dict()["display_host"]
        console.print(f"  current (live)=[bold]{_curr_host}[/bold]  candidate (staging)=[bold]{_cand_host}[/bold]")
        console.print()
        _menu_line("  [s] Current smoke", suffix_markup="[dim]Quick health check on live server — same as Run → [c] Current smoke[/dim]")  # FL-3618: canonical label + cross-ref to prevent drift
        _menu_line("  [p] ⚠ Promote Candidate → Current", suffix_markup="[bold red](irreversible)[/bold red] [dim]Copy staging to live — cannot undo[/dim]")
        _menu_disabled_line("c", "Cherry-pick (blocked)", "not available — use Deploy or Promote instead")
        if topology in CURRENT_DEPLOY_TOPOLOGIES:
            _menu_line("  [d] ⚠ Deploy Current Server", suffix_markup="[dim]Push code to live production server — cannot undo[/dim]")  # FL-1980 pattern: visible danger + consequence before keypress
        _menu_line("  [q] Back")
        choice = _prompt_char("> ")

        if choice == "q":
            return
        if choice == "s":
            if not _require_slot_targets(env, "current", need_ssh=True, need_runtime=True):
                continue
            _run_command(
                _watchdog_run_command(env, mode="current-smoke", slot="current"),
                label="current smoke",
                cwd=REPO_ROOT,
            )
            continue
        if choice == "p":
            if not _require_slot_targets(env, "candidate", need_ssh=True, need_runtime=False):
                continue
            if not _require_slot_targets(env, "current", need_ssh=True, need_runtime=False):
                continue
            if _can_prompt():
                _promote_confirm = _prompt_choice("  Promote candidate to current (live)? [y/N]: ").lower()
                if _promote_confirm != "y":
                    console.print("  [dim]Cancelled.[/dim]")
                    _pause("  Press Enter to continue.")
                    continue
            _run_command(
                [_repo_python(), "scripts/promote_candidate_to_current.py"],
                label="promote candidate to current",
                cwd=REPO_ROOT,
            )
            continue
        if choice == "c":
            console.print("  [yellow]⚠[/yellow]  Cherry-pick is not available. Use Deploy or Promote instead.")
            _pause("  Press Enter to continue.")
            continue
        if choice == "d" and topology in CURRENT_DEPLOY_TOPOLOGIES:
            if not _require_slot_targets(env, "current", need_ssh=True, need_runtime=False):
                continue
            current = _load_slot_config(env, "current").as_dict()
            if _can_prompt():
                confirm = _prompt_choice(f"  ⚠ Deploy to LIVE production server ({current['display_host']}). Confirm? [y/N]: ").lower()
                if confirm != "y":
                    console.print("  [dim]Cancelled.[/dim]")
                    continue
            _run_command(
                [
                    _repo_python(),
                    "scripts/deploy_current_server.py",
                    "--ssh-target",
                    str(current["ssh_target"]),
                ],
                label="deploy current server",
                cwd=REPO_ROOT,
            )
            continue
        console.print(f"  [dim]Unknown key: {choice!r}[/dim]")


def _menu_mobile_playwright(env: dict[str, str]) -> None:
    env = _seed_playwright_defaults(dict(env))
    while True:
        _draw_submenu_header("Mobile / Playwright")
        viewport = env.get("PLAYWRIGHT_VIEWPORT", PLAYWRIGHT_DEFAULTS["PLAYWRIGHT_VIEWPORT"])
        duration = env.get("PLAYWRIGHT_DURATION", PLAYWRIGHT_DEFAULTS["PLAYWRIGHT_DURATION"])
        engine = env.get("PLAYWRIGHT_BROWSER_ENGINE", PLAYWRIGHT_DEFAULTS["PLAYWRIGHT_BROWSER_ENGINE"])
        device = env.get("PLAYWRIGHT_DEVICE", PLAYWRIGHT_DEFAULTS["PLAYWRIGHT_DEVICE"])
        console.print(f"  viewport: {viewport} ({device})")
        console.print(f"  duration: {duration}s")
        console.print(f"  browser:  {engine} (chromium/webkit/firefox)")
        console.print()
        _menu_line("  [r] Run", suffix_markup="[dim]Launch browser-based mobile test[/dim]")
        _menu_line("  [c] Config", suffix_markup="[dim]Edit viewport, duration, browser settings[/dim]")
        _menu_line("  [s] Status", suffix_markup="[dim]Current Playwright config and readiness[/dim]")
        _menu_line("  [q] Back")
        choice = _prompt_char("> ")

        if choice == "q":
            return
        if choice == "r":
            if not _require_slot_targets(env, "candidate", need_ssh=True, need_runtime=True):
                continue
            if not shutil.which("node"):
                console.print("  [red]✗[/red]  Node.js not found. Install via: brew install node (macOS) or nodejs.org")
                _pause("  Press Enter to continue.")
                continue
            command, cfg, error, out_dir = _mobile_playwright_command(env, seed_defaults=False)
            if command is None:
                console.print(f"  [red]✗[/red]  Playwright config error: {error or 'PLAYWRIGHT config invalid'}. Go to [c] Config to fix the settings.")
                _pause("  Press Enter to continue.")
                continue
            if _can_prompt():
                console.print(f"  Run will take ~{duration}s on {PLAYWRIGHT_DEVICE} / {engine} — press [r] to confirm or any other key to cancel. ", end="", markup=False)
                _run_confirm = _prompt_char("")
                if _run_confirm != "r":
                    console.print("  [dim]Cancelled.[/dim]")
                    _pause("  Press Enter to continue.")
                    continue
            _run_command(command, label="mobile playwright", cwd=REPO_ROOT)
            continue
        if choice == "c":
            console.print("  [dim](press Enter to keep current value, q to cancel)[/dim]")
            updated_env = dict(env)
            fields = [
                ("PLAYWRIGHT_VIEWPORT", "  Viewport (WxH, e.g. 375x812)", viewport),
                ("PLAYWRIGHT_DURATION", "  Duration (seconds)", duration),
                ("PLAYWRIGHT_BROWSER_ENGINE", "  Browser engine (chromium/webkit/firefox)", engine),
                ("PLAYWRIGHT_DEVICE", "  Device (e.g. iPhone 14, Pixel 7)", device),
            ]
            cancelled = False
            failed = False
            for key, prompt_text, current_value in fields:
                raw = _prompt_line(prompt_text, current_value)
                if raw.strip().lower() == "q":
                    console.print("  [dim]Cancelled.[/dim]")
                    cancelled = True
                    break
                value = raw if raw.strip() else current_value
                err = _senv.validate_field(key, value)
                if err:
                    console.print(f"  [red]✗[/red]  Invalid value for {labels[key]}: {err}. Press Enter to try again.")
                    failed = True
                else:
                    updated_env[key] = value
            if cancelled:
                continue
            try:
                _senv.save(updated_env)
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                console.print(f"  [red]✗[/red]  Unable to save PLAYWRIGHT_* config: {exc}")
                console.print("  [yellow]⚠[/yellow]  Config not saved — showing last persisted values.")
                env = _seed_playwright_defaults(_senv.load())
                _pause("  Press Enter to continue.")
                continue
            env = _seed_playwright_defaults(_senv.load() or updated_env)
            if failed:
                _pause("  Press Enter to continue.")
            continue
        if choice == "s":
            _show_mobile_status(env, pause_on_success=True)
            continue
        console.print(f"  [dim]Unknown key: {choice!r}[/dim]")


def _menu_multiplayer() -> None:
    while True:
        _draw_submenu_header("Multiplayer")
        with _loading("Checking health"):
            bar = _health.fast_probes()
        remote_badge = _health.menu_badge(bar.multiplayer, bar.multiplayer_detail)
        if remote_badge:
            console.print(f"  Remote/VPS status: {remote_badge}. Local join/host remain available.")
        console.print()
        _menu_line("  [1] Join", suffix_markup="[dim]Connect to a running game server[/dim]")
        host_badge = _health.menu_badge(bar.multiplayer, bar.multiplayer_detail) or ""
        _menu_line("  [2] Host", suffix_markup=host_badge or None)
        # FL-1354: show consequence when test-failed status is displayed
        if "test failed" in host_badge or bar.multiplayer_detail == "test failed":
            console.print("  [dim]Remote server may be unavailable — local play still works.[/dim]")
        _menu_line("  [x] Settings", suffix_markup="[dim]Multiplayer connection settings[/dim]")
        _menu_line("  [q] Back")
        choice = _prompt_char("> ")

        if choice == "q":
            return
        if choice == "x":
            _run_goto("3.1")
            continue
        if choice == "2":
            _menu_multiplayer_host()
            continue
        if choice == "1":
            _menu_multiplayer_join()
            continue
        console.print(f"  [dim]Unknown key: {choice!r}[/dim]")


def _render_health_table(signals: list[_health.HealthSignal] | None = None) -> list:
    if signals is None:
        with _loading("Running health checks"):
            signals = _health.full_health_check()
    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("Signal", style="bold", no_wrap=True)
    table.add_column("Check", style="dim")
    table.add_column("Status", no_wrap=True)
    table.add_column("Detail", style="dim")

    icons = {"ok": "[green]🟢[/green]", "fail": "[red]🔴[/red]", "warn": "[yellow]🟡[/yellow]", "skip": "[dim]N/A[/dim]"}
    for signal in signals:
        table.add_row(signal.name, signal.description, icons.get(signal.status, "?"), signal.detail)
    console.print(table)
    console.print("  [dim]🟢 ok  🔴 broken  🟡 needs attention  N/A = skipped or not configured[/dim]")
    return signals


def _multiplayer_settings_action_lines() -> tuple[tuple[str, str | None], ...]:
    return (
        ("  [r] ⚠ Reset All", "[dim]clears all multiplayer config — cannot undo[/dim]"),  # FL-1980: ⚠ in label so danger is visible before pressing
        ("  [w] multiplayer wizard", "[dim]Guided setup for server connections[/dim]"),
        ("  [h] host server", "[dim]Configure host server address[/dim]"),
        ("  [q] save + back", None),
        ("  [u] Discard + Back", "[dim]Go back without saving changes[/dim]"),
    )


def _edit_multiplayer_settings() -> None:
    if not _can_prompt():
        _print_multiplayer_config_noninteractive()
        return
    env = _senv.load()
    original_env = dict(env)
    labels = {
        "AK_MP_SERVER_TOPOLOGY_TYPE": "Topology (none|full-local|single-vps|hybrid|two-machine)",
        "AK_MP_SERVER_CANDIDATE_HOST": "Candidate host (IP)",
        "AK_MP_SERVER_CANDIDATE_SSH_USER": "Candidate SSH user",
        "AK_MP_SERVER_CANDIDATE_SSH_KEY": "Candidate SSH key (path)",
        "AK_MP_SERVER_CANDIDATE_DOMAIN": "Candidate domain",
        "AK_MP_SERVER_CANDIDATE_PORT": "Candidate port (default 8080)",
        "AK_MP_SERVER_CURRENT_HOST": "Current host (IP)",
        "AK_MP_SERVER_CURRENT_SSH_USER": "Current SSH user",
        "AK_MP_SERVER_CURRENT_SSH_KEY": "Current SSH key (path)",
        "AK_MP_SERVER_CURRENT_DOMAIN": "Current domain",
        "AK_MP_SERVER_CURRENT_PORT": "Current port (default 8080)",
        "AK_MP_SERVER_DNS_PROVIDER": "DNS provider",
        "AK_MP_SERVER_DNS_API_TOKEN": "DNS API token",
        "PLAYWRIGHT_VIEWPORT": "Playwright viewport",
        "PLAYWRIGHT_DURATION": "Playwright duration",
        "PLAYWRIGHT_BROWSER_ENGINE": "Playwright browser engine",
        "PLAYWRIGHT_DEVICE": "Playwright device",
    }
    FIELD_HINTS = {
        "AK_MP_SERVER_CANDIDATE_HOST": "Example: 1.2.3.4",
        "AK_MP_SERVER_CANDIDATE_SSH_USER": "Example: ubuntu",
        "AK_MP_SERVER_CANDIDATE_SSH_KEY": "Example: ~/.ssh/id_rsa",
        "AK_MP_SERVER_CANDIDATE_DOMAIN": "Example: staging.example.com",
        "AK_MP_SERVER_CURRENT_HOST": "Example: 1.2.3.4",
        "AK_MP_SERVER_CURRENT_SSH_USER": "Example: ubuntu",
        "AK_MP_SERVER_CURRENT_SSH_KEY": "Example: ~/.ssh/id_rsa",
        "AK_MP_SERVER_CURRENT_DOMAIN": "Example: game.example.com",
    }
    fields = list(labels.keys())

    while True:
        _draw_submenu_header("Multiplayer Settings")

        def _field_label(key: str) -> str:
            val = env.get(key, "")
            if key in _senv.HIDDEN_FIELDS and val:
                val = "(hidden)"
            return f"{labels[key]:<30}  =  {val or '(not set)'}"

        _mp_field_items = [(key, _field_label(key)) for key in fields]
        _mp_action_items = [
            ("q", "── save + back ──"),
            ("u", "── discard + back ──"),
            ("w", "── multiplayer wizard ──"),
            ("h", "── host server ──"),
            ("r", "── ⚠ reset all ──"),
        ]
        _mp_chosen = _fuzzy_select(
            _mp_field_items + _mp_action_items,
            title="Multiplayer Settings — select field to edit or action",
            label_fn=lambda t: t[1],
            console=console,
            renderer=_renderer,
        )
        choice = _mp_chosen[0] if _mp_chosen is not None else "q"

        if choice == "q":
            _senv.save(env)
            return
        if choice == "u":
            return
        if choice == "w":
            if env != original_env:
                answer = _prompt_choice("  Unsaved field edits will be lost — continue? [y/N]: ").lower()
                if answer != "y":
                    continue
            # Wizard uses input() and prints to stdout — needs full terminal
            if _renderer.active:
                with _renderer.paused():
                    _wizard.run_vps_wizard(_io_mgr._real_console)
            else:
                _wizard.run_vps_wizard(_io_mgr._real_console)
            env = _senv.load()
            original_env = dict(env)
            continue
        if choice == "h":
            if env != original_env:
                answer = _prompt_choice("  Unsaved changes. Save before Host Server? [y] save / [n] discard / [c] cancel: ").lower()
                if answer in {"c", ""}:
                    continue
                if answer == "y":
                    _senv.save(env)
                    original_env = dict(env)
            _menu_multiplayer_host()
            continue
        if choice == "r":
            answer = _prompt_choice("  Clear all multiplayer config? [y/N]: ").lower()
            if answer == "y":
                env = {}
            continue

        if choice not in labels:
            console.print("  [dim]Invalid choice.[/dim]")
            continue
        field = choice

        if field in FIELD_HINTS:
            console.print(f"  [dim]{FIELD_HINTS[field]}[/dim]")
        new_value = _prompt_line(f"  {labels[field]}", env.get(field, ""))
        err = _senv.validate_field(field, new_value)
        if err:
            console.print(f"  [red]✗[/red]  {err}")
            _pause("  Press Enter to continue.")
            continue
        env[field] = new_value


def _edit_blender_paths() -> None:
    while True:
        _draw_submenu_header("Blender & OpenStreetMap Config")
        status = _blender_paths.probe()
        cfg = _lcfg.load()
        blender_ready = bool(status.blender_path)
        online_ready = bool(status.blosm_available and cfg.get("BLOSM_API_KEY"))

        console.print(f"  Blender path: {status.blender_path or '(not found)'}")
        console.print(f"  Blender version: {status.version or '(unknown)'}")
        console.print(f"  Addon profile: {status.addon_profile}")
        console.print(f"  Addon dir: {status.addon_dir or '(not created)'}")
        console.print("  Required addons for detected Blender:")
        for addon in status.required_addons:
            present = status.addons.get(addon, False)
            icon = "[green]🟢[/green]" if present else "[red]🔴[/red]"
            console.print(f"  {icon} {addon}")
        if status.version and status.version.startswith("4.5"):
            console.print("  Blender 2.86 legacy addons in this same dir (not required for 4.5):")
            for addon, present in status.legacy_addons.items():
                icon = "[green]🟢[/green]" if present else "[dim]⚪[/dim]"
                console.print(f"  {icon} {addon}")
        blosm_icon = "[green]🟢[/green]" if status.blosm_available else "[red]🔴[/red]"
        console.print(f"  {blosm_icon} blosm")
        asciiid_icon = "[green]🟢[/green]" if _baked_osm_ready() else "[red]🔴[/red]"
        console.print(f"  {asciiid_icon} ASCIIID editor binary (needed for local bake)")
        if online_ready:
            console.print("  Current mode: Online")
        elif not status.blosm_available:
            console.print("  Current mode: Local pre-processed (blosm addon missing)")
        elif not cfg.get("BLOSM_API_KEY"):
            console.print("  Current mode: Local pre-processed (BLOSM_API_KEY missing)")
        elif not _baked_osm_ready():
            console.print("  Current mode: Local pre-processed (ASCIIID editor missing)")
        else:
            console.print("  Current mode: Local pre-processed")
        key_state = "(hidden)" if cfg.get("BLOSM_API_KEY") else "(not set)"
        console.print(f"  BLOSM_API_KEY: {key_state}")
        console.print("  Online mode needs blosm (Blender OpenStreetMap addon) + BLOSM_API_KEY.")
        console.print("  Local pre-processed mode needs a local .osm file and the ASCIIID editor binary.")
        if status.blosm_available and cfg.get("BLOSM_API_KEY"):
            console.print("  current mode: [dim]Online[/dim]")
        elif _baked_osm_ready():
            console.print("  current mode: [dim]Local pre-processed[/dim]")
        else:
            console.print("  current mode: [dim]not configured[/dim]")
        console.print()
        if blender_ready:
            console.print("  [f] fix addons    run scripts/setup_addon.py")
            console.print("  [k] API key       set or clear BLOSM_API_KEY")
        else:
            _menu_disabled_line("f", "fix addons", "Install Blender first.")
            _menu_disabled_line("k", "API key", "Install Blender first.")
        console.print("  [o] ↗ Open Blender & OpenStreetMap Workflow")
        console.print("  [q] back")
        choice = _prompt_char("> ")

        if choice == "q":
            return
        if choice == "o":
            _menu_blender_osm()
            continue
        if choice == "f":
            if not blender_ready:
                console.print("  [yellow]⚠[/yellow]  Install Blender first. Download from blender.org/download")
                _pause("  Press Enter to continue.")
                continue
            _run_command([_repo_python(), "scripts/setup_addon.py"], label="setup_addon", cwd=REPO_ROOT)
            continue
        if choice == "k":
            if not blender_ready:
                console.print("  [yellow]⚠[/yellow]  Install Blender first. Download from blender.org/download")
                _pause("  Press Enter to continue.")
                continue
            current = cfg.get("BLOSM_API_KEY", "")
            value = _prompt_line("  BLOSM_API_KEY (blank to clear)", current)
            if value == current == "":
                continue
            if value == "":
                cfg.pop("BLOSM_API_KEY", None)
            else:
                err = _lcfg.validate_field("BLOSM_API_KEY", value)
                if err:
                    console.print(f"  [red]✗[/red]  {err}")
                    _pause("  Press Enter to continue.")
                    continue
                cfg["BLOSM_API_KEY"] = value
            _lcfg.save(cfg)
            continue
        console.print(f"  [dim]Unknown key: {choice!r}[/dim]")


def _show_mcp_inventory(*, pause_on_exit: bool = True) -> None:
    _draw_submenu_header("MCP Mount Status (running state)")
    mcp_dir = REPO_ROOT / "docs/agent/mcp"
    scripts = sorted(mcp_dir.glob("*.py")) if mcp_dir.exists() else []
    if not scripts:
        console.print("  [yellow]⚠[/yellow]  No background service scripts found in docs/agent/mcp/. Check repository integrity.")
        if pause_on_exit:
            _pause("  Press Enter to return.")
        return
    console.print("  MCP = background service process Claude can connect to.")
    console.print("  running = detected by a process-name heuristic; not running = not detected.")
    console.print("  Heuristics can false-positive or false-negative.")
    console.print()
    mounted = _mounted_mcp_script_names()
    for script in scripts:
        state = "running" if script.name in mounted else "not running"
        if pause_on_exit:
            console.print(f"  {script.name} [{state}]")
            console.print(f"    path: {script}")
            console.print(f"    run:  {_repo_python()} {script}")
            continue
        _write_stdout_lines([
            f"{script.name} {state}",
            f"path: {script}",
            f"run: {_repo_python()} {script}",
        ])
    if pause_on_exit:
        _pause("  Press Enter to return.")


def _mounted_mcp_script_names() -> set[str]:
    try:
        result = subprocess.run(
            ["ps", "ax", "-o", "command="],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    if result.returncode != 0:
        return set()
    mounted: set[str] = set()
    for script in (REPO_ROOT / "docs/agent/mcp").glob("*.py"):
        script_text = str(script)
        rel_text = str(script.relative_to(REPO_ROOT))
        if any(script_text in line or rel_text in line for line in result.stdout.splitlines()):
            mounted.add(script.name)
    return mounted


def _menu_config_status() -> None:
    cached_health: list[_health.HealthSignal] | None = None
    while True:
        _draw_submenu_header("Config & Status")
        with _loading("Running health checks"):
            cached_health = _health.full_health_check()
            bar = _health.fast_probes()
        console.print("  " + bar.render())
        console.print("  [dim]✓=healthy  ⚠=attention  ✗=broken  🟢=pass  🔴=fail  🟡=warning[/dim]")
        console.print()
        _signals = _render_health_table(cached_health)
        _proof_sig = next((s for s in (_signals or []) if s.name in {"Watchdog proof", "Runtime proof"}), None)
        if _proof_sig and _proof_sig.status == "fail":
            console.print("  [dim]⚠ REGRESSION — check [v] Server Status or [a] Analyze Runs before building[/dim]")
        console.print()
        _menu_line("  [1] Multiplayer Settings  (\u2192 sub-menu)", suffix_markup="[dim]Server addresses, SSH keys, and topology[/dim]")
        _menu_line("  [2] Blender & OpenStreetMap Config", suffix_markup="[dim]Blender path, addons, and map API key[/dim]")
        _menu_line("  [v] Server Status", suffix_markup="[dim]Latest test result, slot liveness, bundle parity[/dim]")
        _menu_line("  [a] Analyze Runs", suffix_markup="[dim]Browse run summaries, logs, artifacts, and metrics[/dim]")
        _menu_line("  [f] Failure Log", suffix_markup="[dim]Browse tracked bugs and failures (FL-NNNN entries)[/dim]")  # FL-3481: shortcut avoids 5-level VPS path
        _menu_line("  [3] Tool Server Status (MCP)", suffix_markup="[dim]Connected automation tools[/dim]")
        _menu_line("  [b] Build Game (~60 s)", suffix_markup="[dim]Compile the game client binary[/dim]")
        _menu_line("  [s] Build Server (~60 s)", suffix_markup="[dim]Compile the multiplayer server binary[/dim]")
        _menu_line("  [h] Expand Health Details", suffix_markup="[dim]Full dependency and service health breakdown[/dim]")
        _menu_line("  [o] Blender & OpenStreetMap Tools", suffix_markup="[dim]Import/export maps using Blender and OpenStreetMap[/dim]")
        _menu_line("  [q] Back")
        console.print()
        console.print("  [dim]↓ scroll if items are cut off on small terminals[/dim]")
        choice = _prompt_char("> ")

        if choice == "q":
            return
        if choice == "1":
            _edit_multiplayer_settings()
        elif choice == "2":
            _edit_blender_paths()
        elif choice == "v":
            _menu_vps_header()
        elif choice == "a":
            _menu_analyze_runs()
        elif choice == "f":
            _menu_failure_log()  # FL-3481: direct shortcut avoids 5-level VPS path
        elif choice == "3":
            _show_mcp_inventory()
        elif choice == "b":
            if _proof_sig and _proof_sig.status == "fail":
                console.print("  [yellow]⚠[/yellow]  Watchdog proof REGRESSION — rebuild may not fix; see watchdog log before building.")
                answer = _prompt_choice("  Build game anyway? [y/N]: ").lower()
                if answer != "y":
                    continue
            _run_command(["make", "-C", str(REPO_ROOT), "game"], label="build game", cwd=REPO_ROOT)
        elif choice == "s":
            if _proof_sig and _proof_sig.status == "fail":
                console.print("  [yellow]⚠[/yellow]  Watchdog proof REGRESSION — rebuild may not fix; see watchdog log before building.")
                answer = _prompt_choice("  Build server anyway? [y/N]: ").lower()
                if answer != "y":
                    continue
            _run_command(["make", "-C", str(REPO_ROOT), "server"], label="build server", cwd=REPO_ROOT)
        elif choice == "h":
            if cached_health is None:
                with _loading("Running health checks"):
                    cached_health = _health.full_health_check()
            _render_health_table(cached_health)
            _pause("  Press Enter to continue.")
        elif choice == "o":
            _run_goto("2.5.b")
        else:
            console.print(f"  [dim]Unknown key: {choice!r}[/dim]")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Asciicker launcher")
    parser.epilog = "Exit codes: 0 success, 1 runtime/config error, 2 argparse usage error, 3 policy-blocked action."
    parser.add_argument("action_name", nargs="?", help="Optional action alias (same value as --action).")
    parser.add_argument("--action", help="Run a launcher action non-interactively when possible.")
    parser.add_argument("--health-json", action="store_true", help="Print full health JSON and exit.")
    # option_tree.py owns the machine-readable launcher schema surfaced by this
    # flag; launcher.py consumes that schema rather than defining it.
    parser.add_argument("--option-tree-json", action="store_true", help="Print launcher option-tree JSON and exit.")
    parser.add_argument("--migration-plan", action="store_true", help="Print launcher migration-plan status and exit.")
    parser.add_argument("--map", dest="map_path", help="Map path for map-editor actions.")
    parser.add_argument("--local-path", help="Local .blend/.osm path for OSM actions.")
    parser.add_argument("--run-id", help="Run ID for multiplayer run-show.")
    parser.add_argument("--latest", action="store_true", help="Use the latest recorded watchdog run when an action accepts --run-id.")
    parser.add_argument("--recipe-name", help="Recipe name for launcher recipe actions.")
    parser.add_argument("--server-url", help="Server URL for join-server-url.")
    parser.add_argument("--target-context", choices=sorted(TARGET_CONTEXT_CHOICES), help="Selected dashboard target context.")
    parser.add_argument("--target-host", help="Custom target host or URL for --target-context custom.")
    parser.add_argument("--port", type=int, help="Local server port for Play With Friends.")
    parser.add_argument("--max-players", type=int, help="Local server max players for Play With Friends.")
    parser.add_argument("--fl-id", help="Failure-log id for FL actions.")
    parser.add_argument("--commits", help="Comma-separated SHAs for cherry-pick actions.")
    parser.add_argument("--term", help="Failure-log search term.")
    parser.add_argument("--gate", help="Watchdog gate name for source/gate actions.")
    parser.add_argument("--match", help="Instance match pattern for delete.")
    parser.add_argument("--mod-dir", help="Bundle mod workspace directory.")
    parser.add_argument("--mod-slug", help="Bundle mod slug.")
    parser.add_argument("--compiled-dir", help="Compiled bundle directory.")
    parser.add_argument("--asset-kind", default="inventory-icon", help="Bundle asset kind for import.")
    parser.add_argument("--slot", help="Bundle slot for import.")
    parser.add_argument("--gameplay-kind", help="Bundle gameplay kind for import.")
    parser.add_argument("--visual-style", help="Bundle visual style for import/preview.")
    parser.add_argument("--contract", help="Bundle asset contract for import.")
    parser.add_argument("--bundle-mode", choices=("safe-replace", "append-content", "profile-swap", "experimental"), default="safe-replace")
    parser.add_argument("--bundle-surface", action="append", default=[], help="Bundle preview surface filter; repeat as needed.")
    parser.add_argument("--bundle-hash-style", choices=("short", "full"), default="short")
    parser.add_argument("--expect-bundle-hash")
    parser.add_argument("--expect-ids-lock-hash")
    parser.add_argument("--bundle-run-watchdog", action="store_true", help="Run bundle verify with --run-watchdog.")
    parser.add_argument("--bundle-skip-build-web", action="store_true", help="Do not pass --run-build-web to bundle verify.")
    parser.add_argument("--bundle-wizard-phase0-only", action="store_true", help="Run bundle wizard Phase 0 initialization only.")
    parser.add_argument("--include-previews", action="store_true", help="Include preview artifacts in bundle packages.")
    parser.add_argument("--profile", help="Appearance profile slug for bundle preview.")
    parser.add_argument("--item", help="Item slug for bundle preview.")
    parser.add_argument("--style", help="Visual style slug for bundle preview.")
    parser.add_argument("--rollback-snapshot", help="Rollback snapshot path for bundle rollback.")
    parser.add_argument("--blend-file", help="Blend file for OSM online import.")
    parser.add_argument("--building", help="OSM building name or id for verification.")
    parser.add_argument("--bbox", help="Optional OSM building bbox selector.")
    parser.add_argument("--meshes-dir", help="Mesh output directory.")
    parser.add_argument("--a3d-output", help="A3D output path.")
    parser.add_argument("--output", help="Output path for copy/export/compare actions.")
    parser.add_argument("--slot-a", help="Slot A selector for XP animation compare; path, filename, or unique substring.")
    parser.add_argument("--slot-b", help="Slot B selector for XP animation compare; path, filename, or unique substring.")
    parser.add_argument("--min-lat")
    parser.add_argument("--max-lat")
    parser.add_argument("--min-lon")
    parser.add_argument("--max-lon")
    parser.add_argument("--tab", choices=("both", "1", "2", "tab1", "tab2"), help="Tab selector for recipe/analyzer actions.")
    parser.add_argument("--from-rel-s", type=float, help="Recipe capture start time relative to run start.")
    parser.add_argument("--to-rel-s", type=float, help="Recipe capture end time relative to run start.")
    parser.add_argument("--description", default="", help="Recipe description.")
    parser.add_argument("--related-fl", action="append", default=[], help="Related FL id for recipe capture; repeatable.")
    parser.add_argument("--mode", choices=("watchdog-only", "full", "local"), default="watchdog-only", help="Recipe repeat mode.")
    parser.add_argument("--controller-hold-open-ms", type=int, default=120000, help="Recipe/manual controller hold-open duration.")
    parser.add_argument("--diff-corpus", choices=DIFF_CORPUS_CHOICES, default="gameplay", help="Relevant source corpus for replay/proof intent.")
    parser.add_argument("--at", type=str, help="server-snapshot relative time selector (wall-clock seconds, e.g. 5.0).")
    parser.add_argument("--tick", type=int, help="server-snapshot tick selector.")
    parser.add_argument("--entity", help="server-snapshot entity selector.")
    parser.add_argument("--browser-engine", help="Browser engine for mobile/playwright actions.")
    parser.add_argument("--grid", default="1")
    parser.add_argument("--material-id", default="1")
    parser.add_argument("--extra-flag", action="append", default=[], help="Extra inspect flag; repeat as needed.")
    parser.add_argument("--no-browser", action="store_true", help="Do not auto-open a browser for workbench actions.")
    parser.add_argument("--no-open", action="store_true", help="Do not open asciiid for OSM verify actions.")
    parser.add_argument("--detach-open", action="store_true", help="Detach asciiid for OSM verify actions.")
    parser.add_argument("--duration", type=float, help="Keep local asset server alive for N seconds before stopping.")
    parser.add_argument("--list-actions", action="store_true", help="List all valid --action names and exit. Use with --json for machine-readable output.")
    parser.add_argument("--json", action="store_true", help="Emit structured output in JSON format (for --list-actions, watchdog-context-json, mobile-status-json).")
    parser.add_argument("--audit-mode", action="store_true",
                        help="Enable audit mode: non-renderer, stdout output, dry-run actions, state isolation.")
    return parser


def _action_map_path(path: str | None) -> str:
    return path or str(DEFAULT_MAP)


def _action_osm_online(args: argparse.Namespace) -> int:
    cfg = _lcfg.load()
    blender = _blender_paths.probe()
    if not blender.blosm_available or not cfg.get("BLOSM_API_KEY"):
        console.print("  [red]✗[/red]  Online Blosm import requires the blosm addon and BLOSM_API_KEY.")
        return 1
    bbox = {
        "min_lat": args.min_lat or "",
        "max_lat": args.max_lat or "",
        "min_lon": args.min_lon or "",
        "max_lon": args.max_lon or "",
    }
    if not all(bbox.values()):
        console.print("  [red]✗[/red]  --min-lat/--max-lat/--min-lon/--max-lon are required.")
        return 1
    bbox_error = _validate_osm_bbox(bbox)
    if bbox_error:
        console.print(f"  [red]✗[/red]  {bbox_error}")
        return 1
    run_paths = _osm_run_paths()
    blend_file = args.blend_file or str(run_paths["blend_file"])
    meshes_dir = args.meshes_dir or str(run_paths["meshes_dir"])
    a3d_output = args.a3d_output or str(run_paths["a3d_output"])
    console.print(
        f"  [dim]Starting OSM online pipeline (Blender + blosm download + A3D export).[/dim]\n"
        f"  [dim]This takes several minutes — progress appears below as each stage runs.[/dim]"
    )
    return _run_osm_commands(_build_osm_online_commands(blend_file, bbox, meshes_dir, a3d_output))


def _action_osm_local(args: argparse.Namespace, *, pipeline_mode: str = "traditional") -> int:
    if not args.local_path:
        console.print("  [red]✗[/red]  --local-path is required.")
        return 1
    resolved = Path(args.local_path).expanduser().resolve()
    if not resolved.exists():
        console.print(f"  [red]✗[/red]  path not found: {resolved}")
        return 1
    if pipeline_mode == "baked":
        if resolved.suffix.lower() != ".osm":
            console.print("  [red]✗[/red]  map-osm-local-baked requires --local-path to point to a .osm file.")
            return 1
        if not _baked_osm_ready():
            console.print("  [red]✗[/red]  ASCIIID editor binary not built — run: make editor")
            return 1
    run_paths = _osm_run_paths()
    blend_file = args.blend_file or str(run_paths["blend_file"])
    meshes_dir = args.meshes_dir or str(run_paths["meshes_dir"])
    a3d_output = args.a3d_output or str(run_paths["a3d_output"])
    try:
        commands = _build_osm_local_commands(
            str(resolved),
            blend_file,
            meshes_dir,
            a3d_output,
            pipeline_mode=pipeline_mode,
        )
    except ValueError as exc:
        console.print(f"  [red]✗[/red]  {exc}")
        return 1
    return _run_osm_commands(commands)


from launcher_actions import ActionRegistry as _ActionRegistry
_action_registry = _ActionRegistry()

# Wire all option-tree actions into the registry so --action works for agents
# FL-AGENT-BRIDGE: auto-register non-interactive handlers from canonical option tree
from launcher_agent_bridge import register_all_actions as _register_all_actions
_register_all_actions(_action_registry, globals())


def _execute_action(args: argparse.Namespace) -> int:
    action_value = args.action or getattr(args, "action_name", None)
    if getattr(args, "list_actions", False):
        return _list_valid_actions(as_json=getattr(args, "json", False))
    if args.health_json or action_value == "health-json":
        _write_stdout_text(json.dumps(_health.full_health_check_json(), indent=2))
        return 0
    if args.option_tree_json or action_value == "option-tree-json":
        _write_stdout_text(json.dumps(_option_tree.option_tree(), indent=2))
        return 0
    if args.migration_plan or action_value == "migration-plan":
        return _print_migration_plan()
    if not action_value:
        return -1

    action = action_value
    if action.startswith("goto:"):
        return _run_goto(action.split(":", 1)[1])
    if action == "map-launch-asciiid":
        action = "map-asciiid"

    result = _action_registry.dispatch(action, args)
    if result is not None:
        return result

    console.print(f"  [red]✗[/red]  Unknown action: {action}")
    return 1


def _renderer_draw_menu(bar: _health.StatusBar) -> None:
    """Render root menu through the unified renderer (R1, R2, R3)."""
    cols = _renderer.cols
    banner_lines = _cached_banner_lines(cols)

    # Build menu content via RichFormatter (R8: Rich as string formatter)
    _rich_fmt.width = cols
    slot_badges = {
        "1": _health.menu_badge(bar.game, bar.game_detail),
        "2": _health.menu_badge(bar.map_tools, bar.map_detail),
        "3": None,
        ">": None,
        "q": None,
    }
    content: list[str] = []
    content.extend(_rich_fmt.format_markup("[dim]SCRIPTS LAUNCHER[/dim]"))
    content.extend(_rich_fmt.format_markup(bar.render()))
    content.extend(_rich_fmt.format_markup(f"[dim]{bar.render_front_door()}[/dim]"))
    content.append("")
    for key, name, desc in MENU_ITEMS:
        padded = _pad_name(name, MENU_NAME_WIDTH)
        badge = escape(f"[{key}]")
        suffix = f" {slot_badges[key]}" if slot_badges.get(key) else ""
        if desc:
            content.extend(_rich_fmt.format_markup(
                f"  [bold red]{badge}[/bold red] [bold]{padded}[/bold]{suffix} {desc}"
            ))
        else:
            content.extend(_rich_fmt.format_markup(
                f"  [bold red]{badge}[/bold red] [bold]{padded}[/bold]{suffix}"
            ))

    _renderer.set_banner(banner_lines)
    _renderer.set_content(content)
    _renderer.set_status("> ")
    _renderer.render()


def _count_menu_content_lines() -> int:
    """Return the number of content lines reserved for menu items + health bar.

    These lines must not be overwritten by input_command()'s rendering.
    """
    # 1 dim tag + 2 health lines + 1 front door + 1 blank + 5 menu items = 10
    return 10


_cmd_registry = None


def _get_cmd_registry():
    global _cmd_registry
    if _cmd_registry is None:
        from scripts.launcher_lib.command_registry import CommandRegistry
        from scripts.launcher_lib.option_tree import build_command_registry as _build_command_registry
        _cmd_registry = _build_command_registry(globals())
    return _cmd_registry


def _run_interactive_by_action(action: str) -> None:
    """Resolve an action string to an option-tree item and run its command.
    Interactive fallback for items whose handler is _run_command.
    """
    from scripts.launcher_lib.option_tree import flatten_options
    for item in flatten_options():
        if item.get("action") == action:
            cmd = item.get("command")
            label = item.get("label", action)
            if cmd:
                resolved: list[str] = []
                defaults = {
                    "<repo>": str(REPO_ROOT),
                    "<repo_root>": str(REPO_ROOT),
                }
                for token in cmd:
                    if token == "<repo>" or token == "<repo_root>":
                        resolved.append(str(REPO_ROOT))
                    elif token.startswith("<") and token.endswith(">"):
                        # Try to resolve from local config / defaults
                        if token == "<port>":
                            resolved.append(str(DEFAULT_LOCAL_WEB_PORT))
                        elif token == "<players>":
                            resolved.append(str(DEFAULT_LOCAL_MAX_PLAYERS))
                        elif token == "<selected-map>":
                            resolved.append(str(DEFAULT_MAP))
                        elif token == "<host-or-url>":
                            env = _senv.load()
                            resolved.append(env.current_host or "127.0.0.1")
                        else:
                            # Leave unresolved -- _run_command will handle or fail
                            resolved.append(token)
                    else:
                        resolved.append(token)
                _run_command(resolved, label=label, cwd=REPO_ROOT)
                return
            break
    console.print(f"  [red]✗[/red]  No command template for action: {action}")


def _prompt_command_root() -> None:
    """Root command-line loop. Invoked as option-tree leaf handler.

    Renders the menu items + command prompt. Single-char dispatch still works
    instantly (1/2/3/q). Typing enters command-line mode with auto-suggest +
    tab finish. Ctrl+C exits launcher.
    """
    cmd_registry = _get_cmd_registry()
    dispatch = {
        "1": _menu_game,
        "2": _menu_asset_map_editor,
        "3": _menu_config_status,
    }
    _rain_valid = set(dispatch) | {"q", ">"} | {chr(c) for c in range(32, 127)}

    while True:
        try:
            with _loading("Checking health"):
                bar = _health.fast_probes()
        except Exception:
            bar = None

        if _renderer.active:
            if bar:
                _renderer_draw_menu(bar)
            _reserved = _count_menu_content_lines()
            first_char = _rain_engine.rain_root_choice(bar, _rain_valid)
            if first_char is None:
                first_char = _renderer.input_char(_rain_valid)
        else:
            if bar:
                _draw_menu(bar)
            _reserved = 0
            first_char = _rain_engine.rain_root_choice(bar, _rain_valid)
            if first_char is None:
                first_char = _prompt_char("> ")

        if first_char is None:
            continue

        if len(first_char) > 1:
            # Virtual keys (up/down/left/right) — ignore at root
            continue

        if first_char in dispatch:
            try:
                dispatch[first_char]()
            except (KeyboardInterrupt, EOFError):
                console.print("\n  [dim](returned to menu)[/dim]")
            except Exception as exc:
                _print_exception(exc)
                _pause("  Press Enter to return to menu.")
            finally:
                if _io_mgr.submenu_buffering:
                    _stop_submenu_buffer()
            continue

        if first_char == "q":
            console.print("  [dim]bye[/dim]")
            return

        if first_char == ">":
            # Enter command-line mode with empty buffer
            first_char = ""

        if first_char == "\n":
            continue

        if first_char == _AUDIT_FLAG_CHAR:
            _flag_issue_prompt()
            continue

        # Command-line mode
        text = _input_mgr.prompt_command_seeded(
            "> ",
            seed=first_char,
            complete_fn=lambda t: [c.name for c, _ in cmd_registry.match(t)],
            reserved_lines=_reserved,
        )

        if not text:
            continue

        if text in ("quit", "exit", "q"):
            console.print("  [dim]bye[/dim]")
            return

        handler, candidates = cmd_registry.dispatch(text)
        if handler is not None:
            try:
                handler()
            except (KeyboardInterrupt, EOFError):
                console.print("\n  [dim](returned to menu)[/dim]")
            except Exception as exc:
                _print_exception(exc)
                _pause("  Press Enter to return to menu.")
            finally:
                if _io_mgr.submenu_buffering:
                    _stop_submenu_buffer()
        elif candidates:
            names = ", ".join(c.name for c in candidates[:4])
            if _renderer.active:
                _renderer.set_status(f"  [yellow]Did you mean:[/yellow] {names}")
                _renderer.render()
            else:
                console.print(f"  [yellow]Did you mean:[/yellow] {names}")
        else:
            if _renderer.active:
                _renderer.set_status(f"  [red]Unknown: {text!r}[/red]")
                _renderer.render()
            else:
                console.print(f"  [red]Unknown: {text!r}[/red]")


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    # ── Audit-mode bootstrap ─────────────────────────────────────────────
    if getattr(args, "audit_mode", False):
        global _AUDIT_MODE, RUN_DIR, TARGET_CONTEXT_PATH
        _AUDIT_MODE = True
        _sync_audit_mode()
        os.environ["ASCIICKER_AUDIT_MODE"] = "1"
        import tempfile as _tmpmod
        state_dir = os.environ.get("LAUNCHER_STATE_DIR")
        if not state_dir:
            state_dir = _tmpmod.mkdtemp(prefix="audit-state-")
            os.environ["LAUNCHER_STATE_DIR"] = state_dir
        _state_path = Path(state_dir)
        _state_path.mkdir(parents=True, exist_ok=True)
        RUN_DIR = _state_path / ".run"
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        TARGET_CONTEXT_PATH = RUN_DIR / "launcher-target-context.json"

    action_rc = _execute_action(args)
    if action_rc >= 0:
        raise SystemExit(action_rc)

    dispatch = {
        "1": _menu_game,
        "2": _menu_asset_map_editor,
        "3": _menu_config_status,
    }

    # Unified renderer path (FL-1924): single altscreen owner for the session
    _use_renderer = _renderer.can_render()
    if _use_renderer:
        _renderer.activate()
        # Register with cli_style.py so Spinner suppresses stdout (FL-1878)
        try:
            from cli_style import register_scroll_region, unregister_scroll_region
            register_scroll_region()
        except ImportError:
            pass

    # Legacy altscreen (only when renderer is not active)
    _altscreen = not _use_renderer and _launcher_altscreen_enabled() and not _rain_ui_enabled()
    if _altscreen:
        sys.stdout.write("\033[?1049h\033[?25l")
        sys.stdout.flush()

    try:
        _prompt_command_root()
    except (KeyboardInterrupt, EOFError):
        console.print("\n  [dim]bye[/dim]")
    finally:
        if _io_mgr.submenu_buffering:
            _stop_submenu_buffer()
        if _use_renderer:
            try:
                from cli_style import unregister_scroll_region
                unregister_scroll_region()
            except ImportError:
                pass
            _renderer.deactivate()
        if _altscreen:
            sys.stdout.write("\033[?1049l\033[?25h")
            sys.stdout.flush()


if __name__ == "__main__":
    main()

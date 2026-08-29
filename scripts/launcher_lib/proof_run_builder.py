"""Proof Run Builder — FL-1601 human-feasible proof run composition.

Layers:
  1. TUI Dashboard — Rich panels showing open FL targets, recent runs, pending commits
  2. Intent Wizard — guided flow with smart-derive defaults
  3. Run Profiles — persistent JSON templates in .run/watchdog-profiles/
  4. Smart Derive Engine — in smart_derive.py (imported, not housed here)

All layers compose watchdog_runner.py CLI arguments and display the full
command before execution. The operator confirms with [Y] or edits before launch.

Architecture:
- This module is lazy-imported only from the proof_run_builder submenu
- Import failure degrades only this submenu, never launcher startup
- Smart derive calls analyze_runs.py and analyze_failure_log.py as subprocesses
- Profiles are stored in .run/watchdog-profiles/, separate from recipe storage
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scripts.launcher_lib import smart_derive

# Add scripts/ to path for cli_style import
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _smart_derive():
    """Lazy import of smart_derive to avoid degrading launcher startup."""
    from scripts.launcher_lib import smart_derive as _sd
    return _sd


PROFILE_DIR = REPO_ROOT / ".run" / "watchdog-profiles"
DUMPSTER_ROOT = Path.home() / "Downloads" / "asciicker-dumpster"


def _derive_with_progress(**kwargs) -> "smart_derive.DerivedRunIntent":
    """Run derive() with a purple SPINNER_BLOCK + progress bar on stderr."""
    try:
        from cli_style import (
            SPINNER_BLOCK, COLOR_PURPLE, progress_bar, _USE_COLOR,
        )
    except ImportError:
        return _smart_derive().derive(**kwargs)

    stream = sys.stderr
    is_tty = _USE_COLOR and getattr(stream, "isatty", lambda: False)()

    def _on_progress(step: int, total: int, label: str) -> None:
        if not is_tty:
            return
        bar = progress_bar(step, total, width=15, color=f"1;{COLOR_PURPLE}")
        ch = SPINNER_BLOCK[step % len(SPINNER_BLOCK)]
        stream.write(f"\r\033[{COLOR_PURPLE}m{ch}\033[0m  {bar}  {label}\033[K")
        stream.flush()

    if is_tty:
        stream.write("\033[?25l")  # hide cursor
        stream.flush()
    try:
        result = _smart_derive().derive(on_progress=_on_progress, **kwargs)
    finally:
        if is_tty:
            stream.write("\r\033[K\033[?25h")  # clear line + show cursor
            stream.flush()
    return result


# ---------------------------------------------------------------------------
# Layer 3: Run Profiles — persistent intent templates
# ---------------------------------------------------------------------------

def _sanitize_profile_name(name: str) -> str:
    """Sanitize profile name to prevent path traversal or filesystem abuse."""
    # Strip path separators and control characters
    sanitized = re.sub(r'[/\\:\x00-\x1f]', '_', name)
    # Remove leading/trailing dots and whitespace
    sanitized = sanitized.strip('. \t')
    if not sanitized or sanitized in ('.', '..'):
        raise ValueError(f"Invalid profile name: {name!r}")
    # Truncate to reasonable length
    return sanitized[:128]


def _ensure_profile_dir() -> Path:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    return PROFILE_DIR


def list_profiles() -> list[dict]:
    """List all saved profiles as [{name, description, fl_targets, path}]."""
    if not PROFILE_DIR.is_dir():
        return []
    profiles = []
    for p in sorted(PROFILE_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            profiles.append({
                "name": data.get("name", p.stem),
                "description": data.get("description", ""),
                "fl_targets": data.get("fl_targets", []),
                "mode": data.get("mode", "full"),
                "target": data.get("target", "candidate"),
                "path": str(p),
            })
        except (json.JSONDecodeError, OSError):
            profiles.append({"name": p.stem, "description": "(invalid JSON)", "path": str(p)})
    return profiles


def load_profile(name: str) -> dict | None:
    """Load a profile by name. Returns None if not found.

    Tries direct filename match first, then scans all profiles for a matching
    'name' field so that list→show round-trips work even if the JSON name field
    and the filename stem diverge.
    """
    name_s = _sanitize_profile_name(name)
    path = PROFILE_DIR / f"{name_s}.json"
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
    # Fallback: scan profiles and match on the 'name' field inside JSON
    if PROFILE_DIR.is_dir():
        for p in PROFILE_DIR.glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if data.get("name") == name:
                    return data
            except (json.JSONDecodeError, OSError):
                continue
    return None


def save_profile(name: str, data: dict) -> Path:
    """Save a profile. Returns the written path."""
    name = _sanitize_profile_name(name)
    _ensure_profile_dir()
    payload = {**data, "name": name}
    path = PROFILE_DIR / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def delete_profile(name: str) -> str | None:
    """Move profile to dumpster. Returns dumpster path or None if not found."""
    name = _sanitize_profile_name(name)
    path = PROFILE_DIR / f"{name}.json"
    if not path.is_file():
        return None
    today = datetime.now().strftime("%Y-%m-%d")
    dest_dir = DUMPSTER_ROOT / f"profile-delete-{today}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / path.name
    shutil.move(str(path), str(dest))
    return str(dest)


def profile_to_derive_kwargs(profile: dict) -> dict:
    """Convert a profile dict to _smart_derive().derive() kwargs."""
    kwargs: dict = {}
    if profile.get("fl_targets"):
        kwargs["fl_targets"] = profile["fl_targets"]
    if profile.get("mode"):
        kwargs["mode"] = profile["mode"]
    if profile.get("target"):
        kwargs["target"] = profile["target"]
    if profile.get("proof_profile"):
        kwargs["proof_profile"] = profile["proof_profile"]
    if profile.get("diff_corpus"):
        kwargs["diff_corpus"] = profile["diff_corpus"]
    # auto_derive_baselines: True means let derive() auto-select
    if not profile.get("auto_derive_baselines", True):
        kwargs["baseline_run_ids"] = profile.get("baseline_runs", [])
    return kwargs


def intent_to_profile_data(intent: smart_derive.DerivedRunIntent) -> dict:
    """Convert a DerivedRunIntent to a saveable profile dict."""
    return {
        "mode": intent.mode,
        "target": intent.target,
        "proof_profile": intent.proof_profile,
        "diff_corpus": intent.diff_corpus,
        "fl_targets": intent.fl_targets,
        "required_fields": intent.required_fields,
        "base_observation": intent.suggested_observation,
        "auto_derive_baselines": True,
        "auto_derive_fix_attempts_from_fl": True,
    }


# ---------------------------------------------------------------------------
# Layer 1: TUI Dashboard — Rich panels within the launcher menu loop
# ---------------------------------------------------------------------------

def render_dashboard_header(console: object, intent: smart_derive.DerivedRunIntent) -> None:
    """Render the Proof Run Builder dashboard panels using Rich."""
    try:
        from rich.table import Table
    except ImportError:
        # Fallback: plain text
        _render_dashboard_plain(console, intent)
        return

    # FL targets panel
    if intent.open_fl_entries:
        fl_table = Table(title="Open FL Targets", show_header=True, header_style="bold")
        fl_table.add_column("FL ID", style="cyan")
        fl_table.add_column("Required Fields", style="white")
        for entry in intent.open_fl_entries:
            fl_table.add_row(
                entry.get("fl_id", "?"),
                str(len(entry.get("required_fields", []))),
            )
        console.print(fl_table)
    elif intent.fl_targets:
        console.print(f"  FL targets: {', '.join(intent.fl_targets)}")
    else:
        console.print("  [dim]No FL targets derived.[/dim]")

    # Recent runs panel
    if intent.recent_runs:
        run_table = Table(title="Recent Runs", show_header=True, header_style="bold")
        run_table.add_column("Run ID", style="cyan")
        run_table.add_column("False Gates", style="red")
        run_table.add_column("Baseline?", style="green")
        for run in intent.recent_runs[:5]:
            run_id = run.get("run_id") or run.get("id") or run.get("label", "?")
            false_gates = run.get("false_gates") or []
            is_baseline = "←" if run_id in intent.baseline_runs else ""
            run_table.add_row(run_id, str(len(false_gates)), is_baseline)
        console.print(run_table)
    else:
        console.print("  [dim]No recent runs found.[/dim]")

    # Pending commits panel
    if intent.pending_commits:
        commit_table = Table(title="Pending Commits (since baseline)", show_header=True, header_style="bold")
        commit_table.add_column("Hash", style="cyan", width=8)
        commit_table.add_column("Subject", style="white")
        commit_table.add_column("Type", style="yellow")
        for commit in intent.pending_commits[:10]:
            ctype = "fix" if commit["hash"] in intent.fix_attempt_refs else "introduced-by"
            commit_table.add_row(
                commit["hash"][:8],
                commit.get("subject", "")[:60],
                ctype,
            )
        console.print(commit_table)

    # Derive errors
    if intent.derive_errors:
        console.print()
        for err in intent.derive_errors:
            console.print(f"  [yellow]⚠[/yellow]  {err}")

    # Required fields summary — FL-1607: group by prefix instead of flat list
    if intent.required_fields:
        groups: dict[str, int] = {}
        for fld in intent.required_fields:
            # Extract prefix: "remote0_foo" → "remote0_*", "local_bar" → "local_*"
            parts = fld.split("_", 1)
            prefix = f"{parts[0]}_*" if len(parts) > 1 else fld
            groups[prefix] = groups.get(prefix, 0) + 1
        # Sort by count descending
        sorted_groups = sorted(groups.items(), key=lambda kv: -kv[1])
        group_text = ", ".join(f"{prefix} ({count})" for prefix, count in sorted_groups[:6])
        if len(sorted_groups) > 6:
            group_text += f", +{len(sorted_groups) - 6} more"
        console.print(f"\n  Required fields ({len(intent.required_fields)}): {group_text}")
    console.print(f"  Diff corpus: {intent.diff_corpus} ({intent.diff_mode})")

    console.print()


def _render_dashboard_plain(console: object, intent: smart_derive.DerivedRunIntent) -> None:
    """Plain-text fallback when Rich Table/Panel are unavailable."""
    console.print("  ── Proof Run Builder ──")
    if intent.fl_targets:
        console.print(f"  FL targets: {', '.join(intent.fl_targets)}")
    if intent.baseline_runs:
        console.print(f"  Baseline runs: {', '.join(intent.baseline_runs)}")
    if intent.pending_commits:
        console.print(f"  Pending commits: {len(intent.pending_commits)}")
    if intent.required_fields:
        console.print(f"  Required fields: {len(intent.required_fields)}")
    console.print(f"  Diff corpus: {intent.diff_corpus}")
    if intent.derive_errors:
        for err in intent.derive_errors:
            console.print(f"  ⚠ {err}")
    console.print()


# ---------------------------------------------------------------------------
# Layer 2: Intent Wizard — 5-step guided flow
# ---------------------------------------------------------------------------

def run_intent_wizard(
    console: object,
    input_fn: Callable[[], str],
    intent: smart_derive.DerivedRunIntent,
    derive_fn: Callable[..., smart_derive.DerivedRunIntent] | None = None,
) -> smart_derive.DerivedRunIntent | None:
    """Interactive wizard that refines a DerivedRunIntent.

    Returns the refined intent, or None if the operator cancels.
    Each step shows smart-derive defaults; Enter accepts, interactive edits override.
    """
    console.print("  ── Intent Wizard ──")
    console.print()

    # Step 1: Select baseline run(s) — FL-1605: show more, filter infra_fail
    console.print("  Step 1/6: Select baseline run(s)")
    runs_with_gates = [r for r in intent.recent_runs if r.get("false_gates") is not None]
    infra_hidden = len(intent.recent_runs) - len(runs_with_gates)
    displayed_runs = runs_with_gates[:15]
    if displayed_runs:
        for i, run in enumerate(displayed_runs, 1):
            run_id = run.get("run_id") or run.get("id") or run.get("label", "?")
            false_gates = run.get("false_gates") or []
            marker = " ← selected" if run_id in intent.baseline_runs else ""
            console.print(f"    [{i}] {run_id}  ({len(false_gates)} false gates){marker}")
        if infra_hidden:
            console.print(f"    [dim]({infra_hidden} infra_fail runs without gate data hidden)[/dim]")
    else:
        if infra_hidden:
            console.print(f"    (no runs with gate data — {infra_hidden} infra_fail runs hidden)")
        else:
            console.print("    (no recent runs available)")

    console.print(f"  Current selection: {', '.join(intent.baseline_runs) or '(none)'}")
    console.print("  Enter run numbers to toggle (e.g. '1,2'), or Enter to accept: ", end="")
    raw = input_fn().strip()
    if raw.lower() == "q":
        return None
    if raw:
        # Parse comma-separated indices
        new_baselines = []
        for part in raw.split(","):
            try:
                idx = int(part.strip()) - 1
                if 0 <= idx < len(displayed_runs):
                    run_id = (
                        displayed_runs[idx].get("run_id")
                        or displayed_runs[idx].get("id")
                        or displayed_runs[idx].get("label", "")
                    )
                    if run_id:
                        new_baselines.append(run_id)
            except ValueError:
                pass
        if new_baselines:
            intent.baseline_runs = new_baselines
    console.print()

    # Step 2: Toggle FL targets — FL-1606: accept bare numbers and ranges
    console.print("  Step 2/6: Toggle FL targets")
    if intent.fl_targets:
        for i, fl in enumerate(intent.fl_targets, 1):
            console.print(f"    [x] {fl}")
    else:
        console.print("    (no FL targets derived — enter FL IDs manually)")
    console.print("  Enter FL IDs to add/remove (e.g. '1559,1560' or '1559-1565'), or Enter to accept: ", end="")
    raw = input_fn().strip()
    if raw.lower() == "q":
        return None
    if raw:
        parsed_ids: list[str] = []
        for part in raw.split(","):
            token = part.strip()
            if not token:
                continue
            # Range: "1559-1565" (no FL- prefix)
            if "-" in token and not token.upper().startswith("FL-"):
                try:
                    lo_s, hi_s = token.split("-", 1)
                    for n in range(int(lo_s), int(hi_s) + 1):
                        parsed_ids.append(f"FL-{n}")
                except ValueError:
                    pass
            else:
                # Bare number or FL-prefixed
                if token.isdigit():
                    token = f"FL-{token}"
                fl_id = token.upper()
                if fl_id.startswith("FL-"):
                    parsed_ids.append(fl_id)
        for fl_id in parsed_ids:
            if fl_id in intent.fl_targets:
                intent.fl_targets.remove(fl_id)
            else:
                intent.fl_targets.append(fl_id)
    console.print()

    # Step 3: Confirm fix-attempt vs. introduced-by commits
    console.print("  Step 3/6: Confirm fix-attempt commits")
    if intent.pending_commits:
        for commit in intent.pending_commits[:10]:
            ctype = "fix" if commit["hash"] in intent.fix_attempt_refs else "introduced-by"
            console.print(f"    [{ctype:14s}] {commit['hash'][:8]} {commit.get('subject', '')[:50]}")
    else:
        console.print("    (no commits since baseline)")
    console.print("  Enter commit hashes to toggle fix/introduced (e.g. '3dcab514'), or Enter to accept: ", end="")
    raw = input_fn().strip()
    if raw.lower() == "q":
        return None
    if raw:
        for part in raw.split(","):
            h = part.strip()
            if h in intent.fix_attempt_refs:
                intent.fix_attempt_refs.remove(h)
                intent.introduced_by_refs.append(h)
            elif h in intent.introduced_by_refs:
                intent.introduced_by_refs.remove(h)
                intent.fix_attempt_refs.append(h)
    console.print()

    # Step 4: Confirm required fields — FL-1607: show grouped summary
    console.print("  Step 4/6: Required fields (auto-derived from FL targets)")
    if intent.required_fields:
        groups: dict[str, int] = {}
        for fld in intent.required_fields:
            parts = fld.split("_", 1)
            prefix = f"{parts[0]}_*" if len(parts) > 1 else fld
            groups[prefix] = groups.get(prefix, 0) + 1
        sorted_groups = sorted(groups.items(), key=lambda kv: -kv[1])
        for prefix, count in sorted_groups[:8]:
            console.print(f"    {prefix:30s} {count}")
        if len(sorted_groups) > 8:
            console.print(f"    ... +{len(sorted_groups) - 8} more groups")
        console.print(f"    Total: {len(intent.required_fields)} fields")
    else:
        console.print("    (none — FL targets may not have required-fields metadata yet)")
    console.print("  Enter additional field names (comma-separated), or Enter to accept: ", end="")
    raw = input_fn().strip()
    if raw.lower() == "q":
        return None
    if raw:
        for part in raw.split(","):
            fld = part.strip()
            if fld and fld not in intent.required_fields:
                intent.required_fields.append(fld)
    console.print()

    console.print("  Step 5/6: Diff corpus")
    console.print(f"    Current: {intent.diff_corpus} ({intent.diff_mode})")
    console.print("  Enter gameplay/watchdog/launcher/all, or Enter to accept: ", end="")
    raw = input_fn().strip().lower()
    if raw == "q":
        return None
    if raw in {"gameplay", "watchdog", "launcher", "all"}:
        if raw != intent.diff_corpus:
            rederive = derive_fn or _derive_with_progress
            intent = rederive(
                mode=intent.mode,
                target=intent.target,
                proof_profile=intent.proof_profile,
                diff_corpus=raw,
            )
        else:
            intent.diff_corpus = raw
            intent.diff_mode = "between_runs" if raw == "all" else "latest_relevant"
    console.print()

    # Step 6: Edit observation text
    console.print("  Step 6/6: Observation text")
    console.print(f"    Current: {intent.suggested_observation or '(empty)'}")
    console.print("  Edit observation (or Enter to accept): ", end="")
    raw = input_fn().strip()
    if raw.lower() == "q":
        return None
    if raw:
        intent.suggested_observation = raw
    console.print()

    return intent


# ---------------------------------------------------------------------------
# Command composition and display
# ---------------------------------------------------------------------------

def format_cli_command(intent: smart_derive.DerivedRunIntent) -> list[str]:
    """Compose the full watchdog_runner.py command from intent."""
    return intent.to_cli_args()


def print_composed_command(console: object, args: list[str]) -> None:
    """Print the composed command in a copyable format."""
    if len(args) < 2:
        console.print(f"  {' '.join(args)}")
        return
    console.print("  ── Composed command ──")
    # Print as multi-line for readability
    parts: list[str] = []
    i = 0
    while i < len(args):
        if i + 1 < len(args) and args[i].startswith("--"):
            parts.append(f"  {args[i]} {args[i + 1]}")
            i += 2
        else:
            parts.append(f"  {args[i]}")
            i += 1
    for j, part in enumerate(parts):
        suffix = " \\" if j < len(parts) - 1 else ""
        console.print(f"  {part}{suffix}")
    console.print()


# ---------------------------------------------------------------------------
# Main menu loop — called from scripts/launcher.py
# ---------------------------------------------------------------------------

def menu_proof_run_builder(
    console: object,
    input_fn: Callable[[], str] = input,
    prompt_char_fn: Callable[[str], str] | None = None,
    run_command_fn: Callable[[list[str], str, Path], None] | None = None,
    draw_header_fn: Callable[[], None] | None = None,
) -> None:
    """Proof Run Builder submenu — the FL-1601 human interface.

    This is the entry point called by scripts/launcher.py when the operator
    navigates to Run Watchdog → Proof Run Builder.
    """
    _prompt = prompt_char_fn or (lambda msg: input_fn())
    # Derive lazily: render menu first, then derive on first action or refresh
    intent: smart_derive.DerivedRunIntent | None = None

    while True:
        # Render header + dashboard
        if draw_header_fn:
            draw_header_fn()
        console.print()
        if intent is not None:
            render_dashboard_header(console, intent)
        else:
            console.print("  [dim]Intent not yet derived — press \\[f] to derive, or pick an action.[/dim]")

        # Menu — FL-1603: escape brackets so Rich doesn't eat them as style tags
        console.print(r"  \[r] Run from visible state" + "  [dim]Launch the auto-suggested test command[/dim]")
        console.print(r"  \[w] Intent Wizard" + "  [dim]Guided 5-step flow to build a targeted test run[/dim]")
        console.print(r"  \[p] Run Profiles" + "  [dim]Manage saved test run configurations[/dim]")
        console.print(r"  \[d] Derive Intent (JSON)" + "  [dim]Show auto-suggested command as JSON (read-only)[/dim]")
        console.print(r"  \[f] Refresh (re-derive from current state)" + "  [dim]Re-analyze state and update the recommendation[/dim]")
        console.print(r"  \[q] Back")
        choice = _prompt("> ").strip().lower()

        if choice == "q":
            return

        if choice == "f":
            intent = _derive_with_progress()
            console.print("  Refreshed.")
            continue

        if choice in {"r", "w"}:
            if intent is None:
                intent = _derive_with_progress()
            active = intent
            if choice == "w":
                refined = run_intent_wizard(console, input_fn, copy.deepcopy(intent))
                if refined is None:
                    continue
                active = refined
            args = format_cli_command(active)
            print_composed_command(console, args)
            console.print("  [Y] Launch  [S] Save as profile  [Q] Cancel")
            confirm = input_fn().strip().lower()
            if confirm == "y" and run_command_fn:
                run_command_fn(args, "proof run", REPO_ROOT)
                intent = _derive_with_progress()
            elif confirm == "s":
                console.print("  Profile name: ", end="")
                name = input_fn().strip()
                if name:
                    save_profile(name, intent_to_profile_data(active))
                    console.print(f"  Saved profile: {name}")
            continue

        if choice == "p":
            _profiles_submenu(console, input_fn, run_command_fn)
            continue

        if choice == "d":
            if intent is None:
                intent = _derive_with_progress()
            console.print(json.dumps(intent.to_dict(), indent=2))
            console.print("  Press Enter to continue.")
            input_fn()
            continue

        console.print(f"  [dim]Unknown key: {choice!r}[/dim]")


def _profiles_submenu(
    console: object,
    input_fn: Callable[[], str],
    run_command_fn: Callable[[list[str], str, Path], None] | None,
) -> None:
    """Profile management submenu."""
    while True:
        profiles = list_profiles()
        console.print()
        console.print("  ── Run Profiles ──")
        if profiles:
            for i, p in enumerate(profiles, 1):
                fls = ", ".join(p.get("fl_targets", [])[:3])
                console.print(f"    [{i}] {p['name']}  {p.get('description', '')}  FL: {fls}")
        else:
            console.print("    (no saved profiles)")
        console.print()
        console.print(r"  \[number] Load profile  \[c] Create from latest  \[d] Delete  \[q] Back")
        console.print("  > ", end="")
        choice = input_fn().strip().lower()

        if choice == "q":
            return

        if choice == "c":
            console.print("  Profile name: ", end="")
            name = input_fn().strip()
            if name:
                intent = _derive_with_progress()
                save_profile(name, intent_to_profile_data(intent))
                console.print(f"  Created profile: {name}")
            continue

        if choice == "d":
            console.print("  Profile name to delete: ", end="")
            name = input_fn().strip()
            if name:
                dest = delete_profile(name)
                if dest:
                    console.print(f"  Moved to dumpster: {dest}")
                else:
                    console.print(f"  Profile not found: {name}")
            continue

        # Try numeric selection
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(profiles):
                profile = load_profile(profiles[idx]["name"])
                if profile:
                    kwargs = profile_to_derive_kwargs(profile)
                    intent = _derive_with_progress(**kwargs)
                    # Apply profile's base observation if present
                    if profile.get("base_observation") and not intent.suggested_observation:
                        intent.suggested_observation = profile["base_observation"]
                    render_dashboard_header(console, intent)
                    args = format_cli_command(intent)
                    print_composed_command(console, args)
                    console.print("  [Y] Launch  [Q] Cancel")
                    console.print("  > ", end="")
                    confirm = input_fn().strip().lower()
                    if confirm == "y" and run_command_fn:
                        run_command_fn(args, f"proof run (profile: {profiles[idx]['name']})", REPO_ROOT)
                else:
                    console.print(f"  Failed to load profile: {profiles[idx]['name']}")
            continue
        except ValueError:
            pass

        console.print(f"  [dim]Unknown key: {choice!r}[/dim]")

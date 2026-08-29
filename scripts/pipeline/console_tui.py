"""Plain console TUI wizard for the Asciicker asset generation pipeline.

A step-loop wizard using only input()/print() and minimal ANSI codes.
No external dependencies beyond the standard library (no Textual, no curses).

Steps:
    1. Intent selection (numbered list)
    2. Configuration prompts (with bracket defaults)
    3. Analysis summary
    4. Confirm + run pipeline
    5. Result summary + open in XP Tool prompt

Navigation: Each step can return BACK to revisit the previous step.
Steps 4-5 (run/result) are past the point of no return and don't offer Back.

Reuses TUIState from tui/state.py for state management and job config creation.
"""

import os
import sys
from pathlib import Path


# Sentinel returned by step functions to navigate backwards.
BACK = "__BACK__"


# --- ANSI helpers (delegated to shared cli_style module, FL-1177) ---
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cli_style import (  # noqa: E402
    _ansi as color,
    clr_bold as bold,
    clr_cyan as cyan,
    clr_dim as dim,
    clr_green as green,  # FL-1177: ANSI color primitive, not a status claim
    clr_red as red,
    clr_yellow as yellow,
)


# --- Input helpers ---

def prompt(label, default=""):
    """Prompt user for a string value with an optional default.

    ANSI color codes are printed separately from the input() prompt string
    to avoid corrupting readline cursor positioning.
    """
    if default:
        print(f"  {label} [{green(default)}]")
        raw = input("  > ").strip()
    else:
        raw = input(f"  {label}: ").strip()
    return raw if raw else default


def prompt_int(label, default=1, minimum=1, maximum=999):
    """Prompt user for an integer value with validation."""
    while True:
        raw = prompt(label, str(default))
        try:
            val = int(raw)
            if minimum <= val <= maximum:
                return val
            print(red(f"    Must be between {minimum} and {maximum}."))
        except ValueError:
            print(red("    Enter a valid integer."))


def prompt_choice(label, options, default=1, allow_back=False):
    """Display numbered options and return the selected value.

    Args:
        label: Header text.
        options: List of (display_name, value) tuples.
        default: 1-based default selection index.
        allow_back: If True, adds a "< Back" option that returns BACK.
    """
    print(f"\n  {bold(label)}")
    for i, (name, _) in enumerate(options, 1):
        marker = cyan("*") if i == default else " "
        print(f"    {marker} [{i}] {name}")

    if allow_back:
        print(f"        [0] {dim('< Back')}")

    while True:
        raw = input(f"  Choice [{default}]: ").strip()
        if not raw:
            return options[default - 1][1]
        try:
            idx = int(raw)
            if allow_back and idx == 0:
                return BACK
            if 1 <= idx <= len(options):
                return options[idx - 1][1]
            max_opt = len(options)
            min_opt = 0 if allow_back else 1
            print(red(f"    Choose {min_opt}-{max_opt}."))
        except ValueError:
            print(red("    Enter a number."))


def prompt_yn(label, default=True):
    """Prompt for yes/no with a default."""
    hint = "Y/n" if default else "y/N"
    print(f"  {label} [{green(hint)}]")
    raw = input("  > ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


def prompt_path(label, default="", filetypes=None):
    """Prompt for a file path with optional Browse picker.

    Attempts to open a tkinter file dialog for browsing. Falls back to
    manual text entry if tkinter is unavailable (e.g. headless SSH).

    Args:
        label: Prompt text.
        default: Default path if user presses Enter.
        filetypes: Optional list of (description, pattern) tuples for
                   the file dialog filter, e.g. [("Images", "*.png *.jpg")].

    Returns:
        Selected file path string, or default if empty.
    """
    has_tk = False
    try:
        import tkinter as _tk
        from tkinter import filedialog as _fd
        # Verify tkinter actually works (fails on headless)
        _root = _tk.Tk()
        _root.withdraw()
        has_tk = True
    except Exception:
        pass

    if has_tk:
        print(f"\n  {bold(label)}")
        print(f"    [1] Type path manually")
        print(f"    [2] Browse...")
        if default:
            print(f"    {dim(f'Default: {default}')}")

        while True:
            raw = input("  Choice [1]: ").strip()
            if not raw or raw == "1":
                _root.destroy()
                return prompt(label, default)
            if raw == "2":
                tk_filetypes = []
                if filetypes:
                    tk_filetypes = filetypes
                tk_filetypes.append(("All files", "*.*"))
                selected = _fd.askopenfilename(filetypes=tk_filetypes)
                _root.destroy()
                if selected:
                    print(f"    Selected: {green(selected)}")
                    return selected
                print(yellow("    No file selected."))
                return prompt(label, default)
            print(red("    Choose 1 or 2."))
    else:
        return prompt(label, default)


# --- Wizard Steps ---

def step_intent(state):
    """Step 1: Choose what to do. Returns BACK or None."""
    print(f"\n{'=' * 50}")
    print(bold("  ASCIICKER ASSET GENERATOR"))
    print(f"{'=' * 50}")
    print(dim("  Plain console wizard — no dependencies required\n"))

    intent = prompt_choice("What would you like to do?", [
        ("Convert a PNG/image to XP sprite", "convert_sheet"),
        ("Render a 3D model (Blender)", "render_3d"),
        ("Create from template", "from_template"),
        ("Custom pipeline run", "custom"),
    ], default=1)

    state.intent = intent
    if intent == "from_template":
        state.template_name = prompt("Template name", "orc")


def step_configure(state):
    """Step 2: Configure asset parameters. Returns BACK or None."""
    print(f"\n{bold('  CONFIGURATION')}")
    print(f"  {'-' * 40}")

    state.name = prompt("Asset name", state.name or "unnamed")

    if state.intent in ("convert_sheet", "custom"):
        while True:
            state.source_path = prompt_path(
                "Input file path",
                default=state.source_path,
                filetypes=[("Images", "*.png *.jpg *.bmp")],
            )
            if not state.source_path or not state.source_path.strip():
                print(red("    Error: Source file path is required."))
                state.source_path = ""
                continue
            if not os.path.exists(state.source_path):
                print(yellow(f"    Warning: '{state.source_path}' not found."))
                resp = prompt("Continue anyway? (y/n)", "n")
                if resp.lower() == "y":
                    break
                state.source_path = ""
                continue
            break
    elif state.intent == "render_3d":
        state.source_type = "blender"
        state.source_path = prompt_path(
            "Blender .blend file",
            default=state.source_path,
            filetypes=[("Blender", "*.blend")],
        )
        if not state.source_path:
            print(red("    A .blend file is required."))
            return BACK
        state.blender_object = prompt("Object name in scene", state.blender_object or "Cube")
        render_res = prompt_choice("Render resolution (pixels per cell)", [
            ("12px/cell (1x) — fast, low detail", 12),
            ("24px/cell (2x) — recommended", 24),
            ("48px/cell (4x) — high detail", 48),
            ("96px/cell (8x) — very high detail, slow", 96),
        ], default=2)
        state.render_resolution = render_res

    state.angles = prompt_int("Number of angles", default=state.angles or 1, minimum=1, maximum=64)
    state.frames = prompt("Animation frames (comma-separated)", state.frames or "1")

    # Keyframe range prompts (BLEND-15-03)
    # When source is Blender and multiple animations exist, offer keyframe mapping
    frames_parsed = state.parse_frames()
    if getattr(state, "source_type", "file") == "blender" and len(frames_parsed) > 1:
        use_kr = prompt_yn("Define Blender keyframe ranges per animation?", default=False)
        if use_kr:
            from scripts.pipeline.schemas import AnimationRange
            ranges = []
            for i, fc in enumerate(frames_parsed):
                anim_label = f"Animation {i + 1} ({fc} frames)"
                name = prompt(f"  {anim_label} — name (optional)", "")
                ks = prompt_int(f"  {anim_label} — start keyframe", default=0, minimum=0, maximum=99999)
                ke = prompt_int(f"  {anim_label} — end keyframe", default=ks, minimum=ks, maximum=99999)
                ranges.append(AnimationRange(
                    count=fc,
                    keyframe_start=ks,
                    keyframe_end=ke,
                    name=name,
                ))
            state.keyframe_ranges = ranges
            print(dim(f"    {len(ranges)} keyframe ranges configured"))

    # Resolution selector (cell size for slicing — not used for Blender render resolution)
    res = prompt_choice("Cell resolution", [
        ("1x (12px cells)", 1),
        ("2x (24px cells) — recommended", 2),
        ("3x (36px cells)", 3),
        ("4x (48px cells)", 4),
        ("6x (72px cells)", 6),
        ("8x (96px cells)", 8),
    ], default=2)
    state.cell_w = 12 * res
    state.cell_h = 12 * res

    state.transparency = prompt_yn("Enable transparency?", default=False)

    # Background mode
    state.bg_mode = prompt_choice("Background mode", [
        ("Key color (magenta)", "key_color"),
        ("Auto-detect", "auto"),
        ("None (keep as-is)", "none"),
    ], default=1)


def step_analyze(state):
    """Step 3: Show analysis summary. Returns BACK or None."""
    print(f"\n{bold('  ANALYSIS SUMMARY')}")
    print(f"  {'-' * 40}")

    frames_parsed = state.parse_frames()

    rows = [
        ("Intent", state.intent),
        ("Name", state.name),
        ("Source", state.source_path or "(none)"),
        ("Angles", str(state.angles)),
        ("Frames", " + ".join(str(f) for f in frames_parsed)),
        ("Cell size", f"{state.cell_w}x{state.cell_h} px" if state.cell_w else "auto"),
        ("Transparency", "Yes" if state.transparency else "No"),
        ("BG mode", state.bg_mode),
    ]

    if state.source_type == "blender":
        rows.insert(3, ("Blender object", state.blender_object or "(default)"))
        render_res = getattr(state, "render_resolution", 24)
        rows.insert(4, ("Render resolution", f"{render_res}px"))

    if state.template_name:
        rows.insert(1, ("Template", state.template_name))

    max_label = max(len(r[0]) for r in rows)
    for label, value in rows:
        print(f"    {label:<{max_label + 2}} {green(value)}")

    result = prompt_choice("Continue?", [
        ("Proceed to run", "proceed"),
        ("Go back and edit", "back"),
    ], default=1)

    if result == "back":
        return BACK


def step_run(state):
    """Step 4: Run the pipeline. No back navigation (point of no return)."""
    print(f"\n  {bold('RUNNING PIPELINE...')}")
    print(f"  {'-' * 40}")

    try:
        job = state.to_job_config()
    except Exception as exc:
        print(red(f"  Config error: {exc}"))
        return False

    try:
        from scripts.pipeline.service.asset_service import AssetService
        service = AssetService()
        result = service.run(job)
        state.last_output = result
        return True
    except Exception as exc:
        print(red(f"  Pipeline error: {exc}"))
        import traceback
        traceback.print_exc()
        return False


def step_result(state):
    """Step 5: Show result and offer to open in XP Tool."""
    print(f"\n{bold('  RESULT')}")
    print(f"  {'-' * 40}")

    result = state.last_output
    if result is None:
        print(red("  No output produced."))
        return

    # Try to extract output path from various result formats
    xp_path = None
    if hasattr(result, "xp_path"):
        xp_path = str(result.xp_path)
    elif hasattr(result, "output_path"):
        xp_path = str(result.output_path)
    elif isinstance(result, dict):
        xp_path = result.get("xp_path") or result.get("output_path")
    elif isinstance(result, (str, Path)) and str(result).endswith(".xp"):
        xp_path = str(result)

    if xp_path:
        print(green(f"  Output: {xp_path}"))
        if os.path.exists(xp_path):
            size_kb = os.path.getsize(xp_path) / 1024
            print(f"  Size:   {size_kb:.1f} KB")
    else:
        print(yellow("  Pipeline completed (check staging directory for output)."))

    # Offer to open in XP Tool
    if xp_path and os.path.exists(xp_path):
        if prompt_yn("\n  Open in XP Tool?", default=True):
            try:
                import subprocess
                script_dir = os.path.dirname(os.path.abspath(__file__))
                subprocess.Popen(
                    [sys.executable, "-m", "scripts.pipeline.xp_tool", xp_path],
                    cwd=os.path.dirname(os.path.dirname(script_dir)),
                )
                print(green("  Launched XP Tool."))
            except Exception as exc:
                print(yellow(f"  Could not launch XP Tool: {exc}"))

    print(f"\n{'=' * 50}")
    print(bold("  Done!"))
    print(f"{'=' * 50}\n")


# --- Main entry point ---

def run_console_tui():
    """Run the wizard as a step loop with back navigation.

    Steps 0-2 (intent, configure, analyze) support BACK navigation.
    Steps 3-4 (run, result) are linear — no going back after run starts.
    """
    from .tui.state import TUIState

    state = TUIState()

    steps = [step_intent, step_configure, step_analyze]
    current = 0

    try:
        # Navigable steps (0-2)
        while 0 <= current < len(steps):
            result = steps[current](state)
            if result == BACK:
                current = max(0, current - 1)
            else:
                current += 1

        # Non-navigable steps (run + result)
        success = step_run(state)
        if success:
            step_result(state)
    except KeyboardInterrupt:
        print(yellow("\n\n  Interrupted."))
        sys.exit(0)
    except EOFError:
        print(yellow("\n\n  Input closed."))
        sys.exit(0)
